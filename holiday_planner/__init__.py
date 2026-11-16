import os
import sqlite3
from datetime import date
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
        database().commit()

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    @app.get("/")
    def index():
        trips = []
        if "user_id" in session:
            trips = database().execute(
                "SELECT id, destination, start_date, end_date FROM trips "
                "WHERE owner_id = ? ORDER BY start_date, id",
                (session["user_id"],),
            ).fetchall()
        return render_template("index.html", trips=trips)

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
        destination = request.form.get("destination", "").strip()
        start = request.form.get("start_date", "")
        end = request.form.get("end_date", "")
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
            valid_dates = start_date.isoformat() == start and end_date.isoformat() == end and end_date >= start_date
        except ValueError:
            valid_dates = False
        if not destination or len(destination) > 120 or not valid_dates:
            flash("Enter a destination and valid dates, with the end date on or after the start date.", "error")
            return redirect(url_for("index"))
        database().execute(
            "INSERT INTO trips (owner_id, destination, start_date, end_date) VALUES (?, ?, ?, ?)",
            (session["user_id"], destination, start, end),
        )
        database().commit()
        flash("Trip added.", "success")
        return redirect(url_for("index"))

    @app.post("/trips/<int:trip_id>/delete")
    @login_required
    def delete_trip(trip_id):
        result = database().execute(
            "DELETE FROM trips WHERE id = ? AND owner_id = ?", (trip_id, session["user_id"])
        )
        database().commit()
        if result.rowcount == 0:
            abort(404)
        flash("Trip deleted.", "success")
        return redirect(url_for("index"))

    return app
