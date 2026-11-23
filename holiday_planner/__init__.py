import os
import sqlite3
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from authlib.integrations.base_client import OAuthError
from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import BadGateway


csrf = CSRFProtect()
oauth = OAuth()
STARTER_CATEGORIES = ("Sight", "Food", "Stay", "Transport", "Activity", "Note")
MAX_IDEA_TITLE_LEN = 160
MAX_IDEA_CATEGORY_LEN = 40
MAX_IDEA_URL_LEN = 500
MAX_IDEA_NOTES_LEN = 2000


def is_safe_http_url(value):
    if not value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        GITHUB_CLIENT_ID=os.environ.get("GITHUB_CLIENT_ID"),
        GITHUB_CLIENT_SECRET=os.environ.get("GITHUB_CLIENT_SECRET"),
        OAUTH_REDIRECT_URI=os.environ.get("OAUTH_REDIRECT_URI"),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(Path(app.instance_path) / "holidays.sqlite3")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") != "0",
    )
    if test_config:
        app.config.update(test_config)
    for key in ("SECRET_KEY", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "OAUTH_REDIRECT_URI"):
        if not app.config.get(key):
            raise RuntimeError(f"{key} must be configured")

    callback_uri = urlsplit(app.config["OAUTH_REDIRECT_URI"])
    if (
        not callback_uri.hostname
        or callback_uri.username
        or callback_uri.password
        or callback_uri.path != "/auth/callback"
        or callback_uri.query
        or callback_uri.fragment
        or (app.config["SESSION_COOKIE_SECURE"] and callback_uri.scheme != "https")
        or (not app.config["SESSION_COOKIE_SECURE"] and
            (callback_uri.scheme != "http" or callback_uri.hostname not in ("localhost", "127.0.0.1")))
    ):
        raise RuntimeError("OAUTH_REDIRECT_URI must be an HTTPS callback, or a local HTTP callback for development")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    oauth.init_app(app)
    oauth.register(
        name="github",
        client_id=app.config["GITHUB_CLIENT_ID"],
        client_secret=app.config["GITHUB_CLIENT_SECRET"],
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user"},
    )

    def database():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE_PATH"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_database(_error):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        database().execute(
            """CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                destination TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL
            )"""
        )
        database().execute(
            """CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY,
                trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idea',
                created_at TEXT NOT NULL
            )"""
        )
        database().execute(
            "CREATE INDEX IF NOT EXISTS idx_ideas_trip_owner ON ideas (trip_id, owner_id, id)"
        )
        database().commit()

    app.jinja_env.globals["is_safe_http_url"] = is_safe_http_url

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    def parse_iso_date(value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.isoformat() == value else None

    def validate_trip_form(form):
        destination = form.get("destination", "").strip()
        start = form.get("start_date", "")
        end = form.get("end_date", "")
        start_date = parse_iso_date(start)
        end_date = parse_iso_date(end)
        valid_dates = bool(start_date and end_date and end_date >= start_date)
        if not destination or len(destination) > 120 or not valid_dates:
            return None
        return destination, start, end

    def form_values_from_request(form):
        return {
            "destination": form.get("destination", ""),
            "start_date": form.get("start_date", ""),
            "end_date": form.get("end_date", ""),
        }

    def get_owned_trip(trip_id):
        trip = database().execute(
            "SELECT id, destination, start_date, end_date FROM trips "
            "WHERE id = ? AND owner_id = ?",
            (trip_id, session["user_id"]),
        ).fetchone()
        if trip is None:
            abort(404)
        return trip

    def get_trip_categories(trip_id, owner_id):
        rows = database().execute(
            "SELECT DISTINCT category FROM ideas WHERE trip_id = ? AND owner_id = ? ORDER BY id",
            (trip_id, owner_id),
        ).fetchall()
        categories = list(STARTER_CATEGORIES)
        seen = {category.casefold() for category in categories}
        for row in rows:
            category = row["category"]
            if category.casefold() not in seen:
                categories.append(category)
                seen.add(category.casefold())
        return categories

    def normalize_idea_category(raw_category, existing_categories):
        cleaned = " ".join(raw_category.split())
        if not cleaned or len(cleaned) > MAX_IDEA_CATEGORY_LEN:
            return None
        lowered = cleaned.casefold()
        for candidate in (*STARTER_CATEGORIES, *existing_categories):
            if candidate.casefold() == lowered:
                return candidate
        return cleaned

    def validate_idea_form(form, existing_categories):
        title = form.get("title", "").strip()
        if not title or len(title) > MAX_IDEA_TITLE_LEN:
            return None, "Enter an idea title (up to 160 characters)."

        raw_custom = form.get("category", "")
        raw_preset = form.get("category_preset", "")
        if raw_preset == "__custom__":
            raw_category = raw_custom
        elif raw_custom.strip():
            raw_category = raw_custom
        else:
            raw_category = raw_preset

        category = normalize_idea_category(raw_category, existing_categories)
        if category is None:
            return None, "Choose a category or enter a custom one (up to 40 characters)."

        url = form.get("url", "").strip()
        if url:
            if len(url) > MAX_IDEA_URL_LEN:
                return None, "URL must be 500 characters or fewer."
            if not is_safe_http_url(url):
                return None, "URL must start with http:// or https://."

        notes = form.get("notes", "").replace("\r\n", "\n").strip()
        if len(notes) > MAX_IDEA_NOTES_LEN:
            return None, "Notes must be 2000 characters or fewer."

        return (title, category, url, notes), None

    @app.get("/")
    def index():
        trips = []
        confirm_delete = None
        add_form = {"destination": "", "start_date": "", "end_date": ""}
        if "user_id" in session:
            trips = database().execute(
                "SELECT id, destination, start_date, end_date FROM trips "
                "WHERE owner_id = ? ORDER BY start_date, id",
                (session["user_id"],),
            ).fetchall()
            confirm_delete = request.args.get("confirm_delete", type=int)
            saved_form = session.pop("add_trip_form", None)
            if isinstance(saved_form, dict):
                add_form = {
                    "destination": str(saved_form.get("destination", "")),
                    "start_date": str(saved_form.get("start_date", "")),
                    "end_date": str(saved_form.get("end_date", "")),
                }
        end_date_min = add_form["start_date"] if parse_iso_date(add_form["start_date"]) else ""
        return render_template(
            "index.html",
            trips=trips,
            confirm_delete=confirm_delete,
            add_form=add_form,
            end_date_min=end_date_min,
        )

    @app.post("/auth/login")
    def login():
        return oauth.github.authorize_redirect(app.config["OAUTH_REDIRECT_URI"])

    @app.get("/auth/callback")
    def callback():
        try:
            token = oauth.github.authorize_access_token()
        except OAuthError:
            flash("Sign-in was not completed. Please try again.", "error")
            return redirect(url_for("index"))
        response = oauth.github.get("user", token=token)
        if response.status_code != 200:
            raise BadGateway("GitHub could not verify your account")
        user = response.json()
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            raise BadGateway("GitHub returned an invalid account")
        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("index"))

    @app.post("/auth/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.post("/trips")
    @login_required
    def add_trip():
        validated = validate_trip_form(request.form)
        if validated is None:
            session["add_trip_form"] = form_values_from_request(request.form)
            flash("Enter a destination and valid dates, with the end date on or after the start date.", "error")
            return redirect(url_for("index"))
        session.pop("add_trip_form", None)
        destination, start, end = validated
        database().execute(
            "INSERT INTO trips (owner_id, destination, start_date, end_date) VALUES (?, ?, ?, ?)",
            (session["user_id"], destination, start, end),
        )
        database().commit()
        flash("Trip added.", "success")
        return redirect(url_for("index"))

    @app.get("/trips/<int:trip_id>")
    @login_required
    def trip_detail(trip_id):
        trip = get_owned_trip(trip_id)
        ideas = database().execute(
            "SELECT id, title, category, url, notes, status, created_at FROM ideas "
            "WHERE trip_id = ? AND owner_id = ? ORDER BY id DESC",
            (trip_id, session["user_id"]),
        ).fetchall()
        categories = get_trip_categories(trip_id, session["user_id"])
        confirm_delete = request.args.get("confirm_delete") == "1"
        edit_form = {
            "destination": trip["destination"],
            "start_date": trip["start_date"],
            "end_date": trip["end_date"],
        }
        saved_form = session.pop(f"edit_trip_form_{trip_id}", None)
        if isinstance(saved_form, dict):
            edit_form = {
                "destination": str(saved_form.get("destination", "")),
                "start_date": str(saved_form.get("start_date", "")),
                "end_date": str(saved_form.get("end_date", "")),
            }
        end_date_min = edit_form["start_date"] if parse_iso_date(edit_form["start_date"]) else ""
        return render_template(
            "trip.html",
            trip=trip,
            ideas=ideas,
            categories=categories,
            confirm_delete=confirm_delete,
            edit_form=edit_form,
            end_date_min=end_date_min,
        )

    @app.post("/trips/<int:trip_id>/ideas")
    @login_required
    def add_idea(trip_id):
        get_owned_trip(trip_id)
        existing_categories = get_trip_categories(trip_id, session["user_id"])
        validated, error = validate_idea_form(request.form, existing_categories)
        if error is not None:
            flash(error, "error")
            return redirect(url_for("trip_detail", trip_id=trip_id))
        title, category, url, notes = validated
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        database().execute(
            "INSERT INTO ideas (trip_id, owner_id, title, category, url, notes, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'idea', ?)",
            (trip_id, session["user_id"], title, category, url, notes, created_at),
        )
        database().commit()
        flash("Idea added.", "success")
        return redirect(url_for("trip_detail", trip_id=trip_id))

    @app.post("/trips/<int:trip_id>/edit")
    @login_required
    def edit_trip(trip_id):
        get_owned_trip(trip_id)
        validated = validate_trip_form(request.form)
        if validated is None:
            session[f"edit_trip_form_{trip_id}"] = form_values_from_request(request.form)
            flash("Enter a destination and valid dates, with the end date on or after the start date.", "error")
            return redirect(url_for("trip_detail", trip_id=trip_id))
        session.pop(f"edit_trip_form_{trip_id}", None)
        destination, start, end = validated
        database().execute(
            "UPDATE trips SET destination = ?, start_date = ?, end_date = ? "
            "WHERE id = ? AND owner_id = ?",
            (destination, start, end, trip_id, session["user_id"]),
        )
        database().commit()
        flash("Trip updated.", "success")
        return redirect(url_for("trip_detail", trip_id=trip_id))

    @app.get("/trips/<int:trip_id>/delete")
    @login_required
    def confirm_delete_trip(trip_id):
        get_owned_trip(trip_id)
        return redirect(url_for("trip_detail", trip_id=trip_id, confirm_delete=1) + "#delete-trip")

    @app.post("/trips/<int:trip_id>/delete")
    @login_required
    def delete_trip(trip_id):
        get_owned_trip(trip_id)
        if request.form.get("confirm") != "1":
            flash("Confirm deletion before removing this trip.", "error")
            if request.form.get("origin") == "index":
                return redirect(url_for("index", confirm_delete=trip_id) + f"#trip-{trip_id}")
            return redirect(url_for("trip_detail", trip_id=trip_id, confirm_delete=1) + "#delete-trip")
        result = database().execute(
            "DELETE FROM trips WHERE id = ? AND owner_id = ?", (trip_id, session["user_id"])
        )
        database().commit()
        if result.rowcount == 0:
            abort(404)
        flash("Trip deleted.", "success")
        return redirect(url_for("index"))

    return app
