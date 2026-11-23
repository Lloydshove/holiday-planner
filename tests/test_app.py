import re
import sqlite3
from unittest.mock import Mock

from flask import request, url_for
import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from holiday_planner import create_app, oauth


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "ALLOWED_GITHUB_IDS": "1,2",
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
    assert b'href="/"' in response.data
    assert b'aria-label="Trip sections"' in response.data
    assert b'action="/trips/1/edit"' not in response.data

    settings_page = client.get("/trips/1?tab=settings")
    assert settings_page.status_code == 200
    assert b'action="/trips/1/edit"' in settings_page.data
    assert b"/trips/1?tab=settings&amp;confirm_delete=1" in settings_page.data
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
    assert response.location.endswith("/trips/1?tab=settings")

    page = client.get("/trips/1?tab=settings")
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
    for field_id in (b'for="idea-title"', b'for="idea-category-preset"', b'for="idea-category-custom"', b'for="idea-url"', b'for="idea-maps-url"', b'for="idea-notes"'):
        assert field_id in page.data
    assert b"Google Maps link (optional)" in page.data
    for starter in (b"Sight", b"Food", b"Stay", b"Transport", b"Activity", b"Note"):
        assert starter in page.data

    res = client.post("/trips/1/ideas", data={
        "title": "  Omoide Yokocho  ",
        "category_preset": "Food",
        "url": "https://example.com/ramen",
        "maps_url": "  https://maps.google.com/?q=Omoide+Yokocho  ",
        "notes": "  Go before 7pm  ",
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b"Idea added." in res.data
    assert b'role="status"' in res.data
    assert b"Omoide Yokocho" in res.data
    assert b'href="https://example.com/ramen"' in res.data
    assert b'href="https://maps.google.com/?q=Omoide+Yokocho"' in res.data
    assert b'aria-label="Open Omoide Yokocho in Google Maps"' in res.data
    assert b">Open in Google Maps</a>" in res.data
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
            "SELECT trip_id, owner_id, title, category, url, maps_url, notes, status, created_at "
            "FROM ideas ORDER BY id"
        ).fetchall()
    assert len(rows) == 5
    assert rows[0][:8] == (
        1,
        1,
        "Omoide Yokocho",
        "Food",
        "https://example.com/ramen",
        "https://maps.google.com/?q=Omoide+Yokocho",
        "Go before 7pm",
        "idea",
    )
    assert rows[0][8]
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
    {"title": "Valid", "category": "Sight", "url": "https://[invalid"},
    {"title": "Valid", "category": "Sight", "url": "https://example.com/\nnewline"},
    {"title": "Valid", "category": "Sight", "url": "https://example.com/" + "a" * 490},
    {"title": "Valid", "category": "Sight", "maps_url": "javascript:alert(1)"},
    {"title": "Valid", "category": "Sight", "maps_url": "data:text/html,<script>alert(1)</script>"},
    {"title": "Valid", "category": "Sight", "maps_url": "ftp://maps.google.com/place"},
    {"title": "Valid", "category": "Sight", "maps_url": "https://"},
    {"title": "Valid", "category": "Sight", "maps_url": "https://[invalid"},
    {"title": "Valid", "category": "Sight", "maps_url": "maps.google.com/?q=tokyo"},
    {"title": "Valid", "category": "Sight", "maps_url": "https://maps.google.com/\nnewline"},
    {"title": "Valid", "category": "Sight", "maps_url": "https://maps.google.com/" + "a" * 480},
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
            "INSERT INTO ideas (trip_id, owner_id, title, category, url, maps_url, notes, status, created_at) "
            "VALUES (1, 1, 'Tampered', 'Note', 'javascript:alert(1)', 'javascript:alert(2)', '', 'idea', '2027-01-01T00:00:00Z')"
        )
        db.commit()
    page = client.get("/trips/1")
    assert b"Tampered" in page.data
    assert b'href="javascript:' not in page.data
    assert b"Open in Google Maps" not in page.data

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
    fresh = client.get("/trips/1?tab=settings")
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
    trip_page = client.get("/trips/1?tab=settings")
    assert b"/static/trip-dates.js" in trip_page.data
    assert b'name="end_date" value="2027-04-10" min="2027-04-01"' in trip_page.data

    js_res = client.get("/static/trip-dates.js")
    assert js_res.status_code == 200
    assert b"endInput.min = startValue" in js_res.data


def test_login_is_required(client):
    assert client.post("/trips").status_code == 302
    assert client.get("/trips/1").status_code == 302
    assert client.post("/trips/1/ideas").status_code == 302
    assert client.post("/trips/1/ideas/1/status").status_code == 302
    assert client.post("/trips/1/ideas/1/edit").status_code == 302
    assert client.post("/trips/1/ideas/1/delete").status_code == 302
    assert client.post("/trips/1/edit").status_code == 302
    assert client.get("/trips/1/delete").status_code == 302
    assert client.post("/trips/1/delete").status_code == 302


def test_oauth_uses_github_id_and_clears_old_session(client, monkeypatch):
    sign_in(client, 1)
    monkeypatch.setattr(oauth.github, "authorize_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": 2}))
    assert client.get("/auth/callback").status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == 2
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
    assert client.post("/trips/1/ideas/1/status", data={}).status_code == 400
    assert client.post("/trips/1/ideas/1/edit", data={}).status_code == 400
    assert client.post("/trips/1/ideas/1/delete", data={}).status_code == 400
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
    status_res = client.post("/trips/1/ideas/1/status", data={
        "csrf_token": token,
        "status": "shortlisted",
    })
    assert status_res.status_code == 302
    edit_idea_res = client.post("/trips/1/ideas/1/edit", data={
        "csrf_token": token,
        "title": "Upper Belvedere",
        "category_preset": "Sight",
    })
    assert edit_idea_res.status_code == 302
    del_idea_res = client.post("/trips/1/ideas/1/delete", data={"csrf_token": token})
    assert del_idea_res.status_code == 302
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


def test_idea_filtering_archived_visibility_and_empty_states(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    client.post("/trips/1/ideas", data={"title": "Fushimi Inari", "category_preset": "Sight"})
    client.post("/trips/1/ideas", data={
        "title": "Nishiki Market",
        "category_preset": "__custom__",
        "category": "Street Food",
    })
    client.post("/trips/1/ideas", data={"title": "Kinkaku-ji", "category_preset": "Sight"})
    client.post("/trips/1/ideas/2/status", data={"status": "shortlisted"})
    client.post("/trips/1/ideas/3/status", data={"status": "archived"})

    # Default view keeps archived items visible with distinct class and Archived badge
    default_page = client.get("/trips/1")
    assert default_page.status_code == 200
    assert b"Fushimi Inari" in default_page.data
    assert b"Nishiki Market" in default_page.data
    assert b"Kinkaku-ji" in default_page.data
    assert b"is-archived" in default_page.data
    assert b"status-archived" in default_page.data
    assert b">Archived</span>" in default_page.data
    assert b"Apply filters" not in default_page.data
    assert b"data-idea-filter-form" in default_page.data
    assert b"/static/idea-filters.js" in default_page.data
    assert b'href="/trips/1#ideas">Clear filters</a>' in default_page.data
    assert b"Active filters:" not in default_page.data

    filters_js = client.get("/static/idea-filters.js")
    assert filters_js.status_code == 200
    assert b"requestSubmit" in filters_js.data
    assert b"change" in filters_js.data

    # Filter by status
    shortlisted_page = client.get("/trips/1?status=shortlisted")
    assert b"Nishiki Market" in shortlisted_page.data
    assert b"Fushimi Inari" not in shortlisted_page.data
    assert b"Kinkaku-ji" not in shortlisted_page.data
    assert b"Active filters:" in shortlisted_page.data
    assert b"Status: Shortlisted" in shortlisted_page.data
    assert b"Clear filters" in shortlisted_page.data

    # Filter by custom category (including case/whitespace normalization)
    custom_cat_page = client.get("/trips/1?category=street+++food")
    assert b"Nishiki Market" in custom_cat_page.data
    assert b"Fushimi Inari" not in custom_cat_page.data
    assert b"Category: Street Food" in custom_cat_page.data

    # Combined status + category filter with empty result
    empty_filtered = client.get("/trips/1?status=archived&category=Street+Food")
    assert empty_filtered.status_code == 200
    assert b"No ideas match the active filters." in empty_filtered.data
    assert b"Clear filters" in empty_filtered.data


def test_idea_status_transitions_aria_labels_and_invalid_status(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Osaka", "start_date": "2027-05-01", "end_date": "2027-05-06"
    })
    client.post("/trips/1/ideas", data={"title": "Dotonbori", "category_preset": "Food"})

    page = client.get("/trips/1")
    for expected_label in (
        b'aria-label="Shortlist Dotonbori"',
        b'aria-label="Schedule Dotonbori"',
        b'aria-label="Archive Dotonbori"',
        b'aria-label="Edit Dotonbori"',
        b'aria-label="Delete Dotonbori"',
        b'aria-label="Confirm delete Dotonbori"',
    ):
        assert expected_label in page.data

    for target_status, expected_badge in (
        ("shortlisted", b"Shortlisted"),
        ("scheduled", b"Scheduled"),
        ("archived", b"Archived"),
        ("idea", b"Idea"),
    ):
        res = client.post("/trips/1/ideas/1/status", data={"status": target_status}, follow_redirects=True)
        assert res.status_code == 200
        assert expected_badge in res.data
        with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
            row = db.execute("SELECT status FROM ideas WHERE id = 1").fetchone()
            assert row == (target_status,)

    # Invalid status is rejected without modifying stored data
    bad = client.post("/trips/1/ideas/1/status", data={"status": "booked"}, follow_redirects=True)
    assert b'role="alert"' in bad.data
    assert b"Choose a valid status" in bad.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT status FROM ideas WHERE id = 1").fetchone() == ("idea",)


def test_idea_editing_validation_and_single_confirmation_deletion(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Sapporo", "start_date": "2027-06-01", "end_date": "2027-06-05"
    })
    client.post("/trips/1/ideas", data={
        "title": "Odori Park",
        "category_preset": "Sight",
        "url": "https://example.com/odori",
        "notes": "Morning walk",
    })
    client.post("/trips/1/ideas", data={"title": "Keep Me", "category_preset": "Note"})

    # Opening inline edit form renders labeled fields and action aria-labels
    edit_view = client.get("/trips/1?edit_idea=1")
    assert edit_view.status_code == 200
    assert b'action="/trips/1/ideas/1/edit"' in edit_view.data
    assert b'for="edit-idea-title-1"' in edit_view.data
    assert b'for="edit-idea-status-1"' in edit_view.data
    assert b'aria-label="Save changes to Odori Park"' in edit_view.data
    assert b'aria-label="Cancel editing Odori Park"' in edit_view.data

    # Valid edit updates all fields, normalizes custom category, and escapes HTML
    res = client.post("/trips/1/ideas/1/edit", data={
        "title": "  Odori & <script>x</script>  ",
        "category_preset": "__custom__",
        "category": "  Winter   Events ",
        "status": "shortlisted",
        "url": "https://example.com/winter",
        "notes": "  Bring gloves  ",
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b"Idea updated." in res.data
    assert b"<script>" not in res.data
    assert b"&lt;script&gt;x&lt;/script&gt;" in res.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        row = db.execute(
            "SELECT title, category, url, notes, status FROM ideas WHERE id = 1"
        ).fetchone()
    assert row == (
        "Odori & <script>x</script>",
        "Winter Events",
        "https://example.com/winter",
        "Bring gloves",
        "shortlisted",
    )

    # Invalid edits leave stored data unchanged
    for bad_payload in (
        {"title": "   ", "category": "Sight", "status": "shortlisted"},
        {"title": "x" * 161, "category": "Sight", "status": "shortlisted"},
        {"title": "Valid", "category": "c" * 41, "status": "shortlisted"},
        {"title": "Valid", "category": "Sight", "url": "javascript:alert(1)", "status": "shortlisted"},
        {"title": "Valid", "category": "Sight", "notes": "n" * 2001, "status": "shortlisted"},
        {"title": "Valid", "category": "Sight", "status": "invalid"},
    ):
        bad_res = client.post("/trips/1/ideas/1/edit", data=bad_payload, follow_redirects=True)
        assert b'role="alert"' in bad_res.data
        with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
            assert db.execute(
                "SELECT title, category, url, notes, status FROM ideas WHERE id = 1"
            ).fetchone() == row

    # Single-confirmation delete removes only the targeted idea
    assert b'class="delete-confirm"' in res.data
    deleted = client.post("/trips/1/ideas/1/delete", follow_redirects=True)
    assert deleted.status_code == 200
    assert b"Idea deleted." in deleted.data
    assert b"Keep Me" in deleted.data
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        remaining = db.execute("SELECT id, title FROM ideas ORDER BY id").fetchall()
    assert remaining == [(2, "Keep Me")]


def test_idea_actions_enforce_cross_user_and_cross_trip_404_isolation(app, client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Lisbon", "start_date": "2027-01-01", "end_date": "2027-01-05"
    })
    client.post("/trips", data={
        "destination": "Porto", "start_date": "2027-02-01", "end_date": "2027-02-05"
    })
    client.post("/trips/1/ideas", data={"title": "Alfama", "category_preset": "Sight"})

    # Same user targeting idea #1 through trip #2 gets 404 without side effects
    assert client.post("/trips/2/ideas/1/status", data={"status": "archived"}).status_code == 404
    assert client.post("/trips/2/ideas/1/edit", data={
        "title": "Hijacked", "category": "Food"
    }).status_code == 404
    assert client.post("/trips/2/ideas/1/delete").status_code == 404

    # Missing idea_id gets 404
    assert client.post("/trips/1/ideas/999/status", data={"status": "archived"}).status_code == 404
    assert client.post("/trips/1/ideas/999/edit", data={
        "title": "Missing", "category": "Food"
    }).status_code == 404
    assert client.post("/trips/1/ideas/999/delete").status_code == 404

    # Different user gets 404
    sign_in(client, 2)
    assert client.post("/trips/1/ideas/1/status", data={"status": "archived"}).status_code == 404
    assert client.post("/trips/1/ideas/1/edit", data={
        "title": "Hijacked", "category": "Food"
    }).status_code == 404
    assert client.post("/trips/1/ideas/1/delete").status_code == 404

    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT title, status FROM ideas WHERE id = 1").fetchone() == ("Alfama", "idea")


def test_idea_maps_url_edit_clearing_and_legacy_db_migration(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE trips (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                destination TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE ideas (
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
        db.execute(
            "INSERT INTO trips (id, owner_id, destination, start_date, end_date) "
            "VALUES (1, 1, 'Berlin', '2027-05-01', '2027-05-05')"
        )
        db.execute(
            "INSERT INTO ideas (id, trip_id, owner_id, title, category, url, notes, status, created_at) "
            "VALUES (1, 1, 1, 'Brandenburg Gate', 'Sight', '', '', 'idea', '2027-01-01T00:00:00Z')"
        )
        db.commit()

    migrated_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "ALLOWED_GITHUB_IDS": "1",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
        "DATABASE_PATH": str(db_path),
    })

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ideas)").fetchall()}
        assert "maps_url" in columns
        assert db.execute("SELECT maps_url FROM ideas WHERE id = 1").fetchone() == ("",)

    migrated_client = migrated_app.test_client()
    sign_in(migrated_client, 1)

    # Pre-existing idea shows edit field without Maps link on card yet
    edit_page = migrated_client.get("/trips/1?edit_idea=1")
    assert edit_page.status_code == 200
    assert b'for="edit-idea-maps-url-1"' in edit_page.data
    assert b"Open in Google Maps" not in edit_page.data

    # Editing adds maps_url and renders link safely
    updated = migrated_client.post("/trips/1/ideas/1/edit", data={
        "title": "Brandenburg Gate",
        "category_preset": "Sight",
        "status": "shortlisted",
        "maps_url": "https://maps.app.goo.gl/example123",
    }, follow_redirects=True)
    assert updated.status_code == 200
    assert b'href="https://maps.app.goo.gl/example123"' in updated.data
    assert b'aria-label="Open Brandenburg Gate in Google Maps"' in updated.data
    assert b'target="_blank" rel="noopener noreferrer"' in updated.data

    # Edit form pre-populates existing maps_url
    edit_populated = migrated_client.get("/trips/1?edit_idea=1")
    assert b'value="https://maps.app.goo.gl/example123"' in edit_populated.data

    # Invalid maps_url during edit rejects without overwriting stored maps_url
    for bad_maps_url in ("javascript:alert(1)", "data:text/plain,hi", "https://maps.google.com/" + "x" * 480):
        bad_res = migrated_client.post("/trips/1/ideas/1/edit", data={
            "title": "Brandenburg Gate",
            "category_preset": "Sight",
            "status": "shortlisted",
            "maps_url": bad_maps_url,
        }, follow_redirects=True)
        assert b'role="alert"' in bad_res.data
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT maps_url FROM ideas WHERE id = 1").fetchone() == (
                "https://maps.app.goo.gl/example123",
            )

    # Clearing maps_url removes the rendered link
    cleared = migrated_client.post("/trips/1/ideas/1/edit", data={
        "title": "Brandenburg Gate",
        "category_preset": "Sight",
        "status": "shortlisted",
        "maps_url": "",
    }, follow_redirects=True)
    assert cleared.status_code == 200
    assert b"Open in Google Maps" not in cleared.data
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT maps_url FROM ideas WHERE id = 1").fetchone() == ("",)


def test_trip_segmented_tabs_render_active_state_and_isolate_sections(client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Kyoto", "start_date": "2027-04-01", "end_date": "2027-04-10"
    })
    client.post("/trips/1/ideas", data={"title": "Philosopher's Path", "category_preset": "Sight"})

    # Default (inbox) view renders segmented nav, desktop 2-pane layout (capture + inbox), and hides settings
    inbox_page = client.get("/trips/1")
    assert inbox_page.status_code == 200
    assert b'aria-label="Trip sections"' in inbox_page.data
    assert b'class="segment-link is-active" href="/trips/1?tab=inbox" aria-current="page"' in inbox_page.data
    assert b'<span class="segment-count">1</span>' in inbox_page.data
    assert b'class="trip-workspace-grid"' in inbox_page.data
    assert b'id="capture-idea"' in inbox_page.data
    assert b'id="ideas"' in inbox_page.data
    assert b'<input type="hidden" name="tab" value="inbox">' in inbox_page.data
    assert b'id="edit-trip-heading"' not in inbox_page.data
    assert b'id="delete-trip"' not in inbox_page.data

    # Invalid tab falls back to inbox safely
    fallback_page = client.get("/trips/1?tab=unknown")
    assert b'class="segment-link is-active" href="/trips/1?tab=inbox" aria-current="page"' in fallback_page.data
    assert b'id="ideas"' in fallback_page.data

    # Capture tab isolates Quick Capture cleanly
    capture_page = client.get("/trips/1?tab=capture")
    assert capture_page.status_code == 200
    assert b'class="segment-link segment-link-capture is-active" href="/trips/1?tab=capture" aria-current="page"' in capture_page.data
    assert b'id="capture-idea"' in capture_page.data
    assert b'<input type="hidden" name="tab" value="capture">' in capture_page.data
    assert b'id="ideas"' not in capture_page.data
    assert b'id="edit-trip-heading"' not in capture_page.data

    # Settings tab isolates Edit trip and Delete trip cleanly
    settings_page = client.get("/trips/1?tab=settings")
    assert settings_page.status_code == 200
    assert b'class="segment-link is-active" href="/trips/1?tab=settings" aria-current="page"' in settings_page.data
    assert b'id="edit-trip-heading"' in settings_page.data
    assert b'id="delete-trip"' in settings_page.data
    assert b'id="capture-idea"' not in settings_page.data
    assert b'id="ideas"' not in settings_page.data


def test_tab_context_preserved_across_actions_filters_and_validation_errors(client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Osaka", "start_date": "2027-05-01", "end_date": "2027-05-08"
    })

    # Submitting Quick Capture from tab=capture preserves capture tab on error and success
    bad_capture = client.post("/trips/1/ideas", data={
        "tab": "capture",
        "title": "Kuromon Market",
        "category_preset": "__custom__",
        "category": "",
        "url": "https://example.com/kuromon",
        "notes": "Morning snacks",
    }, follow_redirects=False)
    assert bad_capture.status_code == 302
    assert "/trips/1?tab=capture" in bad_capture.location

    err_capture_page = client.get(bad_capture.location)
    assert b'role="alert"' in err_capture_page.data
    assert b'aria-current="page"' in err_capture_page.data
    assert b'href="/trips/1?tab=capture" aria-current="page"' in err_capture_page.data
    assert b'value="Kuromon Market"' in err_capture_page.data
    assert b'value="https://example.com/kuromon"' in err_capture_page.data
    assert b"Morning snacks" in err_capture_page.data

    ok_capture = client.post("/trips/1/ideas", data={
        "tab": "capture",
        "title": "Kuromon Market",
        "category_preset": "Food",
    }, follow_redirects=False)
    assert ok_capture.status_code == 302
    assert "/trips/1?tab=capture" in ok_capture.location

    # Submitting Quick Capture from tab=inbox preserves inbox tab
    ok_inbox = client.post("/trips/1/ideas", data={
        "tab": "inbox",
        "title": "Osaka Castle",
        "category_preset": "Sight",
    }, follow_redirects=False)
    assert ok_inbox.status_code == 302
    assert "/trips/1?tab=inbox" in ok_inbox.location

    # Idea status triage, edit, and delete preserve tab=inbox and active filters
    triage_res = client.post("/trips/1/ideas/1/status", data={
        "status": "shortlisted",
        "filter_status": "idea",
        "filter_category": "Food",
    }, follow_redirects=False)
    assert triage_res.status_code == 302
    assert "tab=inbox" in triage_res.location
    assert "status=idea" in triage_res.location
    assert "category=Food" in triage_res.location

    bad_edit_idea = client.post("/trips/1/ideas/1/edit", data={
        "title": "",
        "category_preset": "Food",
        "filter_status": "shortlisted",
        "filter_category": "Food",
    }, follow_redirects=False)
    assert bad_edit_idea.status_code == 302
    assert "tab=inbox" in bad_edit_idea.location
    assert "edit_idea=1" in bad_edit_idea.location
    assert "status=shortlisted" in bad_edit_idea.location

    ok_edit_idea = client.post("/trips/1/ideas/1/edit", data={
        "title": "Kuromon Market Updated",
        "category_preset": "Food",
        "status": "shortlisted",
        "filter_status": "shortlisted",
    }, follow_redirects=False)
    assert ok_edit_idea.status_code == 302
    assert "tab=inbox" in ok_edit_idea.location
    assert "status=shortlisted" in ok_edit_idea.location

    del_idea = client.post("/trips/1/ideas/1/delete", data={
        "filter_status": "shortlisted",
    }, follow_redirects=False)
    assert del_idea.status_code == 302
    assert "tab=inbox" in del_idea.location
    assert "status=shortlisted" in del_idea.location

    # Invalid trip edit, GET delete confirmation, and unconfirmed POST delete stay on tab=settings
    bad_trip_edit = client.post("/trips/1/edit", data={
        "destination": "",
        "start_date": "2027-05-01",
        "end_date": "2027-05-08",
    }, follow_redirects=False)
    assert bad_trip_edit.status_code == 302
    assert bad_trip_edit.location.endswith("/trips/1?tab=settings")

    confirm_get = client.get("/trips/1/delete", follow_redirects=False)
    assert confirm_get.status_code == 302
    assert "tab=settings" in confirm_get.location
    assert "confirm_delete=1" in confirm_get.location

    unconfirmed_del = client.post("/trips/1/delete", data={}, follow_redirects=False)
    assert unconfirmed_del.status_code == 302
    assert "tab=settings" in unconfirmed_del.location
    assert "confirm_delete=1" in unconfirmed_del.location

    # Direct ?confirm_delete=1 automatically activates settings tab
    direct_confirm = client.get("/trips/1?confirm_delete=1")
    assert b'href="/trips/1?tab=settings" aria-current="page"' in direct_confirm.data
    assert b"Are you sure you want to permanently delete" in direct_confirm.data


def test_responsive_assets_and_progressive_category_picker_are_served(client):
    sign_in(client, 1)
    client.post("/trips", data={
        "destination": "Lisbon", "start_date": "2027-01-01", "end_date": "2027-01-05"
    })
    index_page = client.get("/")
    assert b"/static/category-picker.js" in index_page.data
    assert b'class="trips-grid"' in index_page.data

    trip_page = client.get("/trips/1")
    assert b"/static/category-picker.js" in trip_page.data
    assert b"data-category-picker" in trip_page.data
    assert b"data-custom-category-field" in trip_page.data
    assert b"data-desktop-focus-capture" in trip_page.data
    assert b'class="segment-link segment-link-capture"' in trip_page.data

    picker_js = client.get("/static/category-picker.js")
    assert picker_js.status_code == 200
    assert b"__custom__" in picker_js.data
    assert b"customField.hidden" in picker_js.data
    assert b"data-desktop-focus-capture" in picker_js.data

    css_res = client.get("/static/style.css")
    assert css_res.status_code == 200
    assert b"max-width: 1200px" in css_res.data
    assert b"@media (max-width: 1024px)" in css_res.data
    assert b"@media (max-width: 640px)" in css_res.data
    assert b".segmented-nav" in css_res.data
    assert b"grid-template-columns: repeat(2, minmax(0, 1fr))" in css_res.data
    assert b"grid-template-columns: repeat(3, minmax(0, 1fr))" in css_res.data
    assert b".segment-link-capture" in css_res.data
    assert b"max-width: 780px" not in css_res.data
    assert b".trip-workspace-grid" in css_res.data
    assert b"min-height: 44px" in css_res.data


def test_allowed_github_ids_defaults_to_lloydshove_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_GITHUB_IDS", raising=False)
    default_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
        "DATABASE_PATH": str(tmp_path / "default.sqlite3"),
    })
    assert default_app.config["ALLOWED_GITHUB_IDS"] == frozenset({353556})
    default_client = default_app.test_client()

    monkeypatch.setattr(oauth.github, "authorize_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": 353556}))
    assert default_client.get("/auth/callback").status_code == 302
    with default_client.session_transaction() as session:
        assert session["user_id"] == 353556

    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": 1}))
    rejected = default_client.get("/auth/callback")
    assert rejected.status_code == 403
    with default_client.session_transaction() as session:
        assert "user_id" not in session


def test_allowed_github_ids_supports_env_csv_and_iterable(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_IDS", " 353556 , 42 ")
    env_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "SESSION_COOKIE_SECURE": False,
        "DATABASE_PATH": str(tmp_path / "env.sqlite3"),
    })
    assert env_app.config["ALLOWED_GITHUB_IDS"] == frozenset({353556, 42})

    iter_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "ALLOWED_GITHUB_IDS": [7, 8],
        "SESSION_COOKIE_SECURE": False,
        "DATABASE_PATH": str(tmp_path / "iter.sqlite3"),
    })
    assert iter_app.config["ALLOWED_GITHUB_IDS"] == frozenset({7, 8})


def test_non_allowlisted_oauth_login_is_rejected_without_leaking_allowlist(tmp_path, monkeypatch):
    restricted_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "ALLOWED_GITHUB_IDS": "353556,987654",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
        "DATABASE_PATH": str(tmp_path / "restricted.sqlite3"),
    })
    client = restricted_app.test_client()
    sign_in(client, 353556)
    monkeypatch.setattr(oauth.github, "authorize_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(oauth.github, "get", lambda *args, **kwargs: Mock(status_code=200, json=lambda: {"id": 999}))

    response = client.get("/auth/callback")
    assert response.status_code == 403
    assert b'role="alert"' in response.data
    assert b"not authorized" in response.data
    assert b"Sign in with GitHub" in response.data
    assert b"353556" not in response.data
    assert b"987654" not in response.data
    assert b"ALLOWED_GITHUB_IDS" not in response.data
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_existing_session_with_disallowed_user_is_cleared(client):
    sign_in(client, 999)
    index_res = client.get("/")
    assert index_res.status_code == 200
    assert b"Sign in with GitHub" in index_res.data
    with client.session_transaction() as session:
        assert "user_id" not in session

    sign_in(client, 999)
    protected_res = client.get("/trips/1")
    assert protected_res.status_code == 302
    with client.session_transaction() as session:
        assert "user_id" not in session


@pytest.mark.parametrize("bad_allowlist", [
    "",
    "   ",
    "1,",
    ",1",
    "1,,2",
    "0",
    "1,0",
    "-1",
    "+1",
    "abc",
    "1.5",
    "\u00b2",
    [],
    [0],
    [-2],
    [True],
    ["1"],
    None,
    123,
])
def test_invalid_or_empty_allowed_github_ids_fails_closed(tmp_path, bad_allowlist):
    with pytest.raises(RuntimeError, match="ALLOWED_GITHUB_IDS"):
        create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "GITHUB_CLIENT_ID": "test-id",
            "GITHUB_CLIENT_SECRET": "test-secret",
            "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
            "ALLOWED_GITHUB_IDS": bad_allowlist,
            "SESSION_COOKIE_SECURE": False,
            "DATABASE_PATH": str(tmp_path / "bad.sqlite3"),
        })


def test_empty_env_allowed_github_ids_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_IDS", "")
    with pytest.raises(RuntimeError, match="ALLOWED_GITHUB_IDS"):
        create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "GITHUB_CLIENT_ID": "test-id",
            "GITHUB_CLIENT_SECRET": "test-secret",
            "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
            "SESSION_COOKIE_SECURE": False,
            "DATABASE_PATH": str(tmp_path / "empty-env.sqlite3"),
        })


def test_trust_proxy_enables_proxyfix_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    untrusted_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "http://127.0.0.1:5000/auth/callback",
        "ALLOWED_GITHUB_IDS": "1",
        "SESSION_COOKIE_SECURE": False,
        "DATABASE_PATH": str(tmp_path / "untrusted.sqlite3"),
    })
    assert not isinstance(untrusted_app.wsgi_app, ProxyFix)

    @untrusted_app.get("/_test-proxy")
    def untrusted_probe():
        return f"{request.scheme}|{request.host}|{request.remote_addr}|{url_for('callback', _external=True)}"

    untrusted_res = untrusted_app.test_client().get(
        "/_test-proxy",
        headers={
            "Host": "127.0.0.1:5001",
            "X-Forwarded-For": "203.0.113.10",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "planner.example.com",
            "X-Forwarded-Port": "443",
        },
    )
    assert untrusted_res.data.startswith(b"http|127.0.0.1:5001|")

    monkeypatch.setenv("TRUST_PROXY", "1")
    trusted_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-key",
        "GITHUB_CLIENT_ID": "test-id",
        "GITHUB_CLIENT_SECRET": "test-secret",
        "OAUTH_REDIRECT_URI": "https://planner.example.com/auth/callback",
        "ALLOWED_GITHUB_IDS": "1",
        "SESSION_COOKIE_SECURE": True,
        "DATABASE_PATH": str(tmp_path / "trusted.sqlite3"),
    })
    assert isinstance(trusted_app.wsgi_app, ProxyFix)

    @trusted_app.get("/_test-proxy")
    def trusted_probe():
        return f"{request.scheme}|{request.host}|{request.remote_addr}|{url_for('callback', _external=True)}"

    trusted_res = trusted_app.test_client().get(
        "/_test-proxy",
        headers={
            "Host": "127.0.0.1:5001",
            "X-Forwarded-For": "203.0.113.10",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "planner.example.com",
            "X-Forwarded-Port": "443",
        },
    )
    assert trusted_res.data == b"https|planner.example.com|203.0.113.10|https://planner.example.com/auth/callback"

