"""זרימת החיבור לגוגל: כפתור ← גוגל ← callback, עם תשובות מזויפות מגוגל."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import BASE_URL

import oauth
from config import DRIVE_SCOPES, GOOGLE_AUTH_URL, GOOGLE_REVOKE_URL, GOOGLE_TOKEN_URL


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def google(monkeypatch):
    """מזייף את שרת הטוקנים, את /revoke ואת about.get."""
    calls: list[dict] = []
    state = {
        "token": FakeResponse(
            200,
            {
                "access_token": "ya29.access",
                "refresh_token": "1//refresh",
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": " ".join(DRIVE_SCOPES),
            },
        ),
        "email": "Owner@Example.com",
        "revoke_status": 200,
    }

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        assert "params" not in kwargs, "tokens must never be sent in the URL"
        calls.append({"url": url, "data": dict(data or {}), "headers": headers or {}})
        if url == GOOGLE_TOKEN_URL:
            return state["token"]
        if url == GOOGLE_REVOKE_URL:
            return FakeResponse(state["revoke_status"], {})
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(oauth.requests, "post", fake_post)
    monkeypatch.setattr(oauth, "fetch_account_email", lambda access_token: state["email"])
    state["calls"] = calls
    return state


def _start(client) -> dict:
    resp = client.post(f"{BASE_URL}/oauth/start")
    assert resp.status_code == 303
    location = resp.headers["Location"]
    assert location.startswith(GOOGLE_AUTH_URL + "?")
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def test_start_builds_the_google_request(logged_in):
    params = _start(logged_in)
    assert params["client_id"] == "1234-abc.apps.googleusercontent.com"
    assert params["redirect_uri"] == f"{BASE_URL}/oauth/callback"
    assert params["response_type"] == "code"
    assert set(params["scope"].split()) == set(DRIVE_SCOPES)
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert len(params["state"]) >= 32


def test_successful_connection_is_saved_encrypted(app, logged_in, google):
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert resp.status_code == 303 and resp.headers["Location"] == "/settings?connected=1"

    token_call = next(c for c in google["calls"] if c["url"] == GOOGLE_TOKEN_URL)
    assert token_call["data"]["grant_type"] == "authorization_code"
    assert token_call["data"]["redirect_uri"] == f"{BASE_URL}/oauth/callback"

    store = app.extensions["images2pdf"].token_store
    grant = store.load()
    assert grant.refresh_token == "1//refresh"
    assert set(grant.scopes) == set(DRIVE_SCOPES)

    home = logged_in.get(f"{BASE_URL}/settings").get_data(as_text=True)
    assert "O***@Example.com" in home
    assert "1//refresh" not in home


def test_state_cannot_be_replayed(logged_in, google):
    params = _start(logged_in)
    assert logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc").status_code == 303
    again = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert again.status_code == 400


def test_callback_without_valid_state_does_not_call_google(logged_in, google):
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state=forged-state-value-1234&code=4/abc")
    assert resp.status_code == 400
    assert google["calls"] == []


def test_missing_scope_is_rejected_and_revoked(app, logged_in, google):
    google["token"] = FakeResponse(
        200, {"access_token": "ya29.a", "refresh_token": "1//r", "scope": "https://www.googleapis.com/auth/drive.file"}
    )
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert resp.status_code == 400
    assert "צפייה בכל הקבצים בדרייב" in resp.get_data(as_text=True)
    revoke = next(c for c in google["calls"] if c["url"] == GOOGLE_REVOKE_URL)
    assert revoke["data"] == {"token": "1//r"}  # בגוף הבקשה, לא בכתובת
    assert app.extensions["images2pdf"].token_store.load() is None


def test_wrong_account_is_rejected_and_revoked(app, logged_in, google):
    google["email"] = "someone.else@example.com"
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert resp.status_code == 403
    body = resp.get_data(as_text=True)
    assert "someone.else@example.com" not in body and "s***@example.com" in body
    assert any(c["url"] == GOOGLE_REVOKE_URL for c in google["calls"])
    assert app.extensions["images2pdf"].token_store.load() is None


def test_user_denied_consent(app, logged_in, google):
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&error=access_denied")
    assert resp.status_code == 400
    assert "החיבור בוטל" in resp.get_data(as_text=True)
    assert google["calls"] == []


@pytest.mark.parametrize(
    "token_response",
    [
        FakeResponse(400, {"error": "invalid_grant"}),
        FakeResponse(200, ValueError("not json")),
        FakeResponse(200, ["not", "a", "dict"]),
        FakeResponse(200, {"access_token": "", "scope": ""}),
    ],
)
def test_token_endpoint_failures_are_a_clear_page(app, logged_in, google, token_response):
    google["token"] = token_response
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert resp.status_code == 502
    assert app.extensions["images2pdf"].token_store.load() is None


def test_no_refresh_token_is_not_saved(app, logged_in, google):
    google["token"] = FakeResponse(200, {"access_token": "ya29.a", "scope": " ".join(DRIVE_SCOPES)})
    params = _start(logged_in)
    resp = logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    assert resp.status_code == 502
    assert app.extensions["images2pdf"].token_store.load() is None


def test_disconnect_revokes_and_deletes(app, logged_in, google):
    params = _start(logged_in)
    logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    google["calls"].clear()
    resp = logged_in.post(f"{BASE_URL}/oauth/disconnect")
    assert resp.status_code == 303
    assert google["calls"][0] == {
        "url": GOOGLE_REVOKE_URL,
        "data": {"token": "1//refresh"},
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
    }
    assert app.extensions["images2pdf"].token_store.load() is None


def test_disconnect_reports_failed_revoke_honestly(app, logged_in, google):
    params = _start(logged_in)
    logged_in.get(f"{BASE_URL}/oauth/callback?state={params['state']}&code=4/abc")
    google["revoke_status"] = 503
    resp = logged_in.post(f"{BASE_URL}/oauth/disconnect")
    assert resp.status_code == 502
    assert "לא בוטל אצל גוגל" in resp.get_data(as_text=True)
    assert app.extensions["images2pdf"].token_store.load() is None


def test_google_setup_missing_blocks_start(make_app):
    app = make_app(GOOGLE_CLIENT_ID=None)
    c = app.test_client()
    from helpers import PASSWORD

    c.post(f"{BASE_URL}/login", data={"password": PASSWORD})
    resp = c.post(f"{BASE_URL}/oauth/start")
    assert resp.status_code == 503
    assert "GOOGLE_CLIENT_ID" in resp.get_data(as_text=True)
