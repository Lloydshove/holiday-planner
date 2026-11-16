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
    sign_in(client, 2)
    assert b"Lisbon" not in client.get("/").data
    assert client.post("/trips/1/delete").status_code == 404
    with sqlite3.connect(app.config["DATABASE_PATH"]) as db:
        assert db.execute("SELECT destination FROM trips").fetchall() == [("Lisbon",)]
    sign_in(client, 1)
    assert client.post("/trips/1/delete").status_code == 302
    assert b"Lisbon" not in client.get("/").data


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


def test_login_is_required(client):
    assert client.post("/trips").status_code == 302
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
    assert client.post("/auth/logout").status_code == 400


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
