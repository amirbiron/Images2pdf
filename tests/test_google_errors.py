"""כשל בחידוש ההרשאה, דרך הקוד האמיתי של google-auth.

לא בונים RefreshError ביד: מזייפים רק את התשובה של שרת הטוקנים
(`google.oauth2._client._token_endpoint_request_no_throw`), ונותנים ל-google-auth
לבנות את החריגה בעצמו. כך הבדיקה תיכשל אם צורת החריגה תשתנה בגרסה הבאה.
"""

from __future__ import annotations

import pytest
from google.auth.exceptions import RefreshError
from helpers import BASE_URL

import google_api
from token_store import StoredGrant

GRANT = StoredGrant(
    refresh_token="1//refresh",
    email="owner@example.com",
    scopes=("https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive.readonly"),
    connected_at="2026-11-05T10:00:00+00:00",
)


@pytest.fixture
def token_server(monkeypatch):
    reply = {"value": (False, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, False)}
    calls = []

    def fake(request, token_uri, body, *args, **kwargs):
        calls.append(body)
        return reply["value"]

    monkeypatch.setattr("google.oauth2._client._token_endpoint_request_no_throw", fake)
    reply["calls"] = calls
    google_api.forget_cached_clients()
    yield reply
    google_api.forget_cached_clients()


def _raise_from_real_library(app) -> RefreshError:
    with app.test_request_context("/"):
        with pytest.raises(RefreshError) as info:
            google_api.get_drive_service().about().get(fields="user(emailAddress)").execute()
    return info.value


@pytest.mark.parametrize(
    "response, kind",
    [
        ((False, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, False), "invalid_grant"),
        ((False, {"error": "invalid_grant", "error_subtype": "invalid_rapt"}, False), "invalid_grant"),
        ((False, {"error": "invalid_client", "error_description": "Unauthorized"}, False), "invalid_client"),
        ((False, {"error": "internal_failure"}, True), "other"),
        ((False, "<html>bad gateway</html>", True), "other"),
    ],
)
def test_classification_matches_library_exceptions(app, token_server, response, kind):
    app.extensions["images2pdf"].token_store.save(GRANT)
    token_server["value"] = response
    exc = _raise_from_real_library(app)
    assert google_api.refresh_error_kind(exc) == kind
    assert token_server["calls"][0]["refresh_token"] == "1//refresh"


def test_invalid_grant_disconnects_and_shows_reconnect(app, logged_in, token_server):
    store = app.extensions["images2pdf"].token_store
    store.save(GRANT)
    resp = logged_in.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": "abcDEF123_-"})
    assert resp.status_code == 401
    body = resp.get_data(as_text=True)
    assert "צריך לחבר מחדש" in body and 'action="/oauth/start"' in body
    assert store.load() is None


def test_invalid_grant_on_api_route_is_json(app, logged_in, token_server):
    app.extensions["images2pdf"].token_store.save(GRANT)
    resp = logged_in.get(f"{BASE_URL}/api/drive/thumbnail/abcDEF123")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "google_reconnect_required"}


@pytest.mark.parametrize(
    "response, status",
    [
        ((False, {"error": "invalid_client"}, False), 503),
        ((False, {"error": "internal_failure"}, True), 503),
    ],
)
def test_other_refresh_failures_keep_the_grant(app, logged_in, token_server, response, status):
    store = app.extensions["images2pdf"].token_store
    store.save(GRANT)
    token_server["value"] = response
    resp = logged_in.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": "abcDEF123_-"})
    assert resp.status_code == status
    assert store.load() == GRANT


def test_not_connected_shows_connect_screen(logged_in):
    resp = logged_in.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": "abcDEF123_-"})
    assert resp.status_code == 409
    assert 'action="/oauth/start"' in resp.get_data(as_text=True)


def test_unreadable_grant_asks_to_reconnect(make_app, data_dir):
    first = make_app()
    first.extensions["images2pdf"].token_store.save(GRANT)
    app = make_app(TOKEN_ENCRYPTION_KEY="z" * 44)  # המפתח הוחלף
    c = app.test_client()
    from helpers import PASSWORD

    c.post(f"{BASE_URL}/login", data={"password": PASSWORD})
    resp = c.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": "abcDEF123_-"})
    assert resp.status_code == 409
    assert "TOKEN_ENCRYPTION_KEY" in resp.get_data(as_text=True)
