import re
import sqlite3
from unittest.mock import Mock

import pytest

from holiday_planner import create_app, oauth


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
        "DATABASE_PATH": str(tmp_path / "test.sqlite3"),
    })


@pytest.fixture
def client(app):
    return app.test_client()


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id


def test_trip_storage_is_isolated_and_persistent(app, client):
    sign_in(client, 1)
    response = client.post("/trips", data={
        "destination": "Lisbon", "start_date": "2027-01-01", "end_date": "2027-01-05"
    }, follow_redirects=True)
    assert b"Lisbon" in response.data
    assert b'href="/trips/1"' in response.data
    sign_in(client, 2)
    assert b"Lisbon" not in client.get("/").data
    assert client.get("/trips/1").status_code == 404
    assert client.post("/trips/1/edit", data={
        "destination": "Hijack", "start_date": "2027-02-01", "end_date": "2027-02-05"
    }).status_code == 404
    assert client.get("/trips/1/delete").status_code == 404
    assert client.post("/trips/1/delete", data={"confirm": "1"}).status_code == 404
    assert client.post("/trips/1/delete").status_code == 404
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT destination FROM trips").fetchall() == [("Lisbon",)]
    sign_in(client, 1)
    assert client.post("/trips/1/delete", data={"confirm": "1"}).status_code == 302
    assert b"Lisbon" not in client.get("/").data


def test_trip_workspace_displays_details_edit_form_and_navigation(client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    response = client.get("/trips/1")
    assert response.status_code == 200
    assert b"Kyoto" in response.data
    assert b"2027-04-01" in response.data
    assert b"2027-04-10" in response.data
    assert b'action="/trips/1/edit"' in response.data
    assert b'href="/"' in response.data
    assert b"/trips/1?confirm_delete=1" in response.data
    assert client.get("/trips/999").status_code == 404


def test_trip_edit_persists_and_escapes_html(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    response = client.post("/trips/1/edit", data={
        "destination": "  Osaka & <script>alert(1)</script>  ",
        "start_date": "2027-05-01",
        "end_date": "2027-05-12",
    }, follow_redirects=False)
    assert response.status_code == 302
    assert response.location.endswith("/trips/1")

    page = client.get("/trips/1")
    assert b"Trip updated." in page.data
    assert b"<script>" not in page.data
    assert b"&lt;script&gt;" in page.data
    assert b"2027-05-01" in page.data
    assert b"2027-05-12" in page.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        row = db.execute("SELECT destination, start_date, end_date FROM trips WHERE id = 1").fetchone()
        assert row == ("Osaka & <script>alert(1)</script>", "2027-05-01", "2027-05-12")


@pytest.mark.parametrize("destination,start,end", [
    ("", "2027-04-01", "2027-04-10"),
    ("   ", "2027-04-01", "2027-04-10"),
    ("Osaka", "not-a-date", "2027-04-10"),
    ("Osaka", "2027-04-10", "2027-04-01"),
    ("x" * 121, "2027-04-01", "2027-04-10"),
])
def test_invalid_trip_edits_flash_error_without_modifying_data(app, client, destination, start, end):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    response = client.post("/trips/1/edit", data={
        "destination": destination, "start_date": start, "end_date": end
    }, follow_redirects=True)
    assert b"Enter a destination and valid dates" in response.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        row = db.execute("SELECT destination, start_date, end_date FROM trips WHERE id = 1").fetchone()
        assert row == ("Kyoto", "2027-04-01", "2027-04-10")


def test_two_step_delete_confirmation_from_index_and_workspace(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Oslo", "start_date": "2027-06-01", "end_date": "2027-06-05"
    })

    # Unconfirmed POST does not delete the trip
    unconfirmed = client.post("/trips/1/delete", data={"origin": "index"}, follow_redirects=True)
    assert b"Confirm deletion before removing this trip." in unconfirmed.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1

    # Step 1 on index shows confirmation prompt and cancel link without deleting
    index_confirm = client.get("/?confirm_delete=1")
    assert b"Confirm delete" in index_confirm.data
    assert b"Cancel" in index_confirm.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1

    # Step 1 on workspace shows confirmation prompt without deleting
    workspace_confirm = client.get("/trips/1?confirm_delete=1")
    assert b"Are you sure you want to permanently delete" in workspace_confirm.data
    assert b"Confirm delete" in workspace_confirm.data

    # Step 2 deliberate submit deletes the trip
    deleted = client.post("/trips/1/delete", data={"confirm": "1"}, follow_redirects=True)
    assert b"Trip deleted." in deleted.data
    assert b"No trips yet" in deleted.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0


def test_trip_deletion_cascades_to_ideas(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Rome", "start_date": "2027-07-01", "end_date": "2027-07-07"
    })
    client.post("/trips/1/ideas", data={
        "title": "Colosseum",
        "category_preset": "Sight",
    })
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM ideas WHERE trip_id = 1").fetchone()[0] == 1
    assert client.post("/trips/1/delete", data={"confirm": "1"}).status_code == 302
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0


def test_idea_quick_capture_starter_and_custom_categories_and_escaping(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Tokyo", "start_date": "2027-09-01", "end_date": "2027-09-10"
    })

    page = client.get("/trips/1")
    assert page.status_code == 200
    assert b'action="/trips/1/ideas"' in page.data
    for field_id in (b'for="idea-title"', b'for="idea-category-preset"', b'for="idea-category-custom"', b'for="idea-url"', b'for="idea-notes"'):
        assert field_id in page.data
    for starter in (b"Sight", b"Food", b"Stay", b"Transport", b"Activity", b"Note"):
        assert starter in page.data

    res = client.post("/trips/1/ideas", data={
        "title": "  Omoide Yokocho  ",
        "category_preset": "Food",
        "url": "https://example.com/ramen",
        "notes": "  Go before 7pm  ",
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b"Idea added." in res.data
    assert b'role="status"' in res.data
    assert b"Omoide Yokocho" in res.data
    assert b'href="https://example.com/ramen"' in res.data
    assert b'rel="noopener noreferrer"' in res.data
    assert b"Go before 7pm" in res.data

    # Custom category with duplicate whitespace, then case/whitespace variant + starter case variant
    client.post("/trips/1/ideas", data={
        "title": "Yanaka Ginza",
        "category_preset": "__custom__",
        "category": "  Street   Food  ",
    })
    client.post("/trips/1/ideas", data={
        "title": "Ameyoko Market",
        "category": "street   food",
    })
    client.post("/trips/1/ideas", data={
        "title": "Meiji Shrine",
        "category": "  SIGHT  ",
    })
    xss_page = client.post("/trips/1/ideas", data={
        "title": "<script>alert('title')</script>",
        "category": "<script>cat</script>",
        "notes": "<script>note</script>",
    }, follow_redirects=True)
    assert b"<script>" not in xss_page.data
    assert b"&lt;script&gt;alert(&#39;title&#39;)&lt;/script&gt;" in xss_page.data
    assert b"&lt;script&gt;cat&lt;/script&gt;" in xss_page.data

    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        rows = db.execute(
            "SELECT trip_id, owner_id, title, category, url, notes, status, created_at "
            "FROM ideas ORDER BY id"
        ).fetchall()
    assert len(rows) == 5
    assert rows[0][:7] == (1, 1, "Omoide Yokocho", "Food", "https://example.com/ramen", "Go before 7pm", "idea")
    assert rows[0][7]
    assert rows[1][3] == "Street Food"
    assert rows[2][3] == "Street Food"
    assert rows[3][3] == "Sight"


@pytest.mark.parametrize("payload", [
    {"title": "", "category": "Food"},
    {"title": "   ", "category": "Food"},
    {"title": "x" * 161, "category": "Food"},
    {"title": "Valid", "category": ""},
    {"title": "Valid", "category": "   "},
    {"title": "Valid", "category_preset": "__custom__", "category": "   "},
    {"title": "Valid", "category": "c" * 41},
    {"title": "Valid", "category": "Sight", "url": "javascript:alert(1)"},
    {"title": "Valid", "category": "Sight", "url": "data:text/html,<script>alert(1)</script>"},
    {"title": "Valid", "category": "Sight", "url": "ftp://example.com/map"},
    {"title": "Valid", "category": "Sight", "url": "https://"},
    {"title": "Valid", "category": "Sight", "url": "https://example.com/\nnewline"},
    {"title": "Valid", "category": "Sight", "url": "https://example.com/" + "a" * 490},
    {"title": "Valid", "category": "Sight", "notes": "n" * 2001},
])
def test_invalid_ideas_are_rejected_without_saving(app, client, payload):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Lisbon", "start_date": "2027-01-01", "end_date": "2027-01-05"
    })
    res = client.post("/trips/1/ideas", data=payload, follow_redirects=True)
    assert b"Idea added." not in res.data
    assert b'role="alert"' in res.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 0


def test_ideas_enforce_cross_user_isolation_and_safe_link_rendering(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Lisbon", "start_date": "2027-01-01", "end_date": "2027-01-05"
    })
    # Defense in depth: even a tampered non-http(s) DB URL is never rendered as an anchor
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        db.execute(
            "INSERT INTO ideas (trip_id, owner_id, title, category, url, notes, status, created_at) "
            "VALUES (1, 1, 'Tampered', 'Note', 'javascript:alert(1)', '', 'idea', '2027-01-01T00:00:00Z')"
        )
        db.commit()
    page = client.get("/trips/1")
    assert b"Tampered" in page.data
    assert b'href="javascript:' not in page.data

    sign_in(client, 2)
    assert client.post("/trips/1/ideas", data={
        "title": "Hijacked idea", "category": "Food"
    }).status_code == 404
    assert client.post("/trips/999/ideas", data={
        "title": "Missing trip", "category": "Food"
    }).status_code == 404
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0] == 1


@pytest.mark.parametrize("destination,start,end", [
    ("", "2027-01-01", "2027-01-02"),
    ("Paris", "not-a-date", "2027-01-02"),
    ("Paris", "2027-01-02", "2027-01-01"),
    ("x" * 121, "2027-01-01", "2027-01-02"),
])
def test_invalid_trips_are_rejected(client, destination, start, end):
    sign_in(client, 1)
    response = client.post("/trips", data={
        "destination": destination, "start_date": start, "end_date": end
    }, follow_redirects=True)
    assert b"Enter a destination and valid dates" in response.data
    assert b"No trips yet" in response.data


def test_invalid_add_trip_preserves_entered_values_once(client):
    sign_in(client, 1)
    response = client.post("/trips", data={
        "destination": "Reykjavik",
        "start_date": "2027-08-10",
        "end_date": "2027-08-05",
    }, follow_redirects=True)
    assert b"Enter a destination and valid dates" in response.data
    assert b'value="Reykjavik"' in response.data
    assert b'value="2027-08-10"' in response.data
    assert b'value="2027-08-05"' in response.data
    assert b'min="2027-08-10"' in response.data

    # Subsequent GET starts fresh without stale failed form data
    fresh = client.get("/")
    assert b'value="Reykjavik"' not in fresh.data


def test_invalid_add_trip_escapes_preserved_destination(client):
    sign_in(client, 1)
    response = client.post("/trips", data={
        "destination": '"><script>alert(1)</script>',
        "start_date": "2027-08-10",
        "end_date": "2027-08-05",
    }, follow_redirects=True)
    assert b"<script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_invalid_edit_trip_preserves_entered_values_once(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    response = client.post("/trips/1/edit", data={
        "destination": "Sapporo",
        "start_date": "2027-04-15",
        "end_date": "2027-04-10",
    }, follow_redirects=True)
    assert b"Enter a destination and valid dates" in response.data
    assert b'value="Sapporo"' in response.data
    assert b'value="2027-04-15"' in response.data
    assert b'value="2027-04-10"' in response.data
    assert b'min="2027-04-15"' in response.data

    # Stored trip remains unchanged and next GET shows stored values
    fresh = client.get("/trips/1")
    assert b'value="Kyoto"' in fresh.data
    assert b'value="2027-04-01"' in fresh.data
    assert b'min="2027-04-01"' in fresh.data


def test_date_chooser_constraints_and_script_are_rendered(client):
    sign_in(client, 1)
    index_page = client.get("/")
    assert b"/static/trip-dates.js" in index_page.data
    assert b"data-trip-date-form" in index_page.data

    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    trip_page = client.get("/trips/1")
    assert b"/static/trip-dates.js" in trip_page.data
    assert b'name="end_date" value="2027-04-10" min="2027-04-01"' in trip_page.data

    js_res = client.get("/static/trip-dates.js")
    assert js_res.status_code == 200
    assert b"endInput.min = startValue" in js_res.data


def test_login_is_required(client):
    assert client.post("/trips").status_code == 302
    assert client.get("/trips/1").status_code == 302
    assert client.post("/trips/1/ideas").status_code == 302
    assert client.post("/trips/1/edit").status_code == 302
    assert client.get("/trips/1/delete").status_code == 302
    assert client.post("/trips/1/delete").status_code == 302


def test_oauth_uses_github_id_and_clears_old_session(client, monkeypatch):
    sign_in(client, 99)
    monkeypatch.setattr(oauth.github, "authorize_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": 123}))
    assert client.get("/auth/callback").status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == 123
        assert "access_token" not in session


def test_invalid_github_identity_is_rejected(client, monkeypatch):
    monkeypatch.setattr(oauth.github, "authorize_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": True}))
    assert client.get("/auth/callback").status_code == 502


def test_csrf_protects_mutations(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    sign_in(client, 1)
    assert client.post("/trips", data={}).status_code == 400
    assert client.post("/trips/1/ideas", data={}).status_code == 400
    assert client.post("/trips/1/edit", data={}).status_code == 400
    assert client.post("/trips/1/delete", data={"confirm": "1"}).status_code == 400
    assert client.post("/auth/logout").status_code == 400

    index_page = client.get("/")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', index_page.data).group(1).decode()
    client.post("/trips", data={
        "csrf_token": token,
        "destination": "Vienna",
        "start_date": "2027-08-01",
        "end_date": "2027-08-04",
    })
    workspace_page = client.get("/trips/1")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', workspace_page.data).group(1).decode()
    idea_res = client.post("/trips/1/ideas", data={
        "csrf_token": token,
        "title": "Belvedere Palace",
        "category_preset": "Sight",
    })
    assert idea_res.status_code == 302
    edit_res = client.post("/trips/1/edit", data={
        "csrf_token": token,
        "destination": "Salzburg",
        "start_date": "2027-08-01",
        "end_date": "2027-08-05",
    })
    assert edit_res.status_code == 302
    confirm_page = client.get("/trips/1?confirm_delete=1")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', confirm_page.data).group(1).decode()
    del_res = client.post("/trips/1/delete", data={"csrf_token": token, "confirm": "1"})
    assert del_res.status_code == 302


def test_oauth_redirect_uses_configured_callback_and_validates_state(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    page = client.get("/", headers={"Host": "untrusted.example"})
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post("/auth/login", data={"csrf_token": token},
                           headers={"Host": "untrusted.example"})
    assert response.status_code == 302
    assert "github.com/login/oauth/authorize" in response.location
    assert "127.0.0.1" in response.location
    assert "untrusted.example" not in response.location
    response = client.get("/auth/callback?code=invalid&state=invalid", follow_redirects=True,
                          headers={"Host": "untrusted.example"})
    assert b"Sign-in was not completed" in response.data
    with client.session_transaction(base_url="http://untrusted.example") as session:
        assert "user_id" not in session


def test_destination_is_html_escaped(client):
    sign_in(client, 1)
    response = client.post("/trips", data={
        "destination": "<script>alert(1)</script>",
        "start_date": "2027-01-01",
        "end_date": "2027-01-02",
    }, follow_redirects=True)
    assert b"<script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_callback_cannot_be_http_in_production(app):
    config = dict(app.config)
    config["SESSION_COOKIE_SECURE"] = True
    with pytest.raises(RuntimeError, match="OAUTH_REDIRECT_URI"):
        create_app(config)
