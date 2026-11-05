"""כניסה, השער שלפני כל נתיב, הגבלת ניסיונות ו-CSRF."""

from __future__ import annotations

import threading
from html.parser import HTMLParser

import pytest
from helpers import BASE_URL, PASSWORD

from auth import COOKIE_NAME


class _FormParser(HTMLParser):
    """שולף את הטופס מה-HTML שנוצר, כמו שהדפדפן רואה אותו."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append({"action": a.get("action"), "method": (a.get("method") or "get").lower(), "inputs": {}})
        elif tag == "input" and self.forms:
            self.forms[-1]["inputs"][a.get("name")] = {"type": a.get("type"), "value": a.get("value") or ""}


def _login_form(client) -> dict:
    page = client.get(f"{BASE_URL}/login?next=/dev/checks")
    assert page.status_code == 200
    parser = _FormParser()
    parser.feed(page.get_data(as_text=True))
    forms = [f for f in parser.forms if any(i["type"] == "password" for i in f["inputs"].values())]
    assert len(forms) == 1
    return forms[0]


def test_login_through_the_rendered_form_sets_hardened_cookie(client):
    form = _login_form(client)
    assert form["method"] == "post"
    password_field = next(n for n, i in form["inputs"].items() if i["type"] == "password")
    data = {n: i["value"] for n, i in form["inputs"].items() if i["type"] == "hidden"}
    data[password_field] = PASSWORD

    resp = client.post(f"{BASE_URL}{form['action']}", data=data)
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/dev/checks"
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert PASSWORD.encode("utf-8").hex() not in cookie  # העוגייה היא HMAC, לא הסיסמה

    assert client.get(f"{BASE_URL}/dev/checks").status_code == 200


def test_password_never_travels_in_the_url(client):
    form = _login_form(client)
    assert form["method"] == "post"
    assert "token" not in (form["action"] or "")


def test_wrong_hebrew_password_is_401_not_500(client):
    resp = client.post(f"{BASE_URL}/login", data={"password": "סיסמה-אחרת"})
    assert resp.status_code == 401
    assert "הסיסמה שגויה" in resp.get_data(as_text=True)


def test_overlong_password_is_refused_not_truncated(client):
    resp = client.post(f"{BASE_URL}/login", data={"password": PASSWORD + "x" * 2000})
    assert resp.status_code == 400


@pytest.mark.parametrize("secret", [None, "", "short"])
def test_missing_or_invalid_secret_blocks_everything(make_app, secret):
    app = make_app(WEB_PANEL_SECRET=secret)
    c = app.test_client()
    for path in ("/", "/login", "/dev/checks", "/oauth/callback?state=x"):
        assert c.get(f"{BASE_URL}{path}").status_code == 503, path
    assert c.post(f"{BASE_URL}/login", data={"password": "anything"}).status_code == 503
    api = c.get(f"{BASE_URL}/api/drive/thumbnail/abc")
    assert api.status_code == 503 and api.get_json() == {"error": "service_not_configured"}
    assert c.get(f"{BASE_URL}/healthz").status_code == 200
    assert c.get(f"{BASE_URL}/static/style.css").status_code == 200


def test_unauthenticated_requests(client):
    resp = client.get(f"{BASE_URL}/dev/checks")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=/dev/checks")
    api = client.get(f"{BASE_URL}/api/drive/thumbnail/abc")
    assert api.status_code == 401 and api.get_json() == {"error": "unauthorized"}
    assert client.get(f"{BASE_URL}/oauth/callback?state=x&code=y").status_code == 302
    assert client.post(f"{BASE_URL}/oauth/start").status_code == 303


def test_static_prefix_does_not_open_protected_routes(client):
    resp = client.get(f"{BASE_URL}/static/../dev/checks")
    assert resp.status_code != 200
    assert "בדיקות שלב 0" not in resp.get_data(as_text=True)


@pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example/", "javascript:alert(1)"])
def test_next_cannot_redirect_off_site(client, target):
    resp = client.post(f"{BASE_URL}/login", data={"password": PASSWORD, "next": target})
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/"


def test_logout_clears_cookie(logged_in):
    resp = logged_in.post(f"{BASE_URL}/logout")
    assert resp.status_code == 303
    assert f"{COOKIE_NAME}=;" in resp.headers["Set-Cookie"]
    assert logged_in.get(f"{BASE_URL}/").status_code == 302


def test_rate_limit_holds_under_parallel_attempts(app):
    """30 ניסיונות שגויים במקביל: בדיוק 10 נבדקים, והשאר מקבלים 429."""
    barrier = threading.Barrier(30)
    statuses: list[int] = []
    lock = threading.Lock()

    def attempt():
        c = app.test_client()
        barrier.wait()
        code = c.post(f"{BASE_URL}/login", data={"password": "wrong-password"}).status_code
        with lock:
            statuses.append(code)

    threads = [threading.Thread(target=attempt) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses.count(401) == 10
    assert statuses.count(429) == 20


def test_rate_limit_survives_worker_restart(make_app):
    first = make_app()
    c = first.test_client()
    for _ in range(10):
        assert c.post(f"{BASE_URL}/login", data={"password": "wrong-password"}).status_code == 401
    second = make_app()  # כמו worker חדש אחרי max_requests: אותו דיסק, זיכרון נקי
    resp = second.test_client().post(f"{BASE_URL}/login", data={"password": PASSWORD})
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


def test_successful_login_resets_the_counter(client):
    for _ in range(5):
        client.post(f"{BASE_URL}/login", data={"password": "wrong-password"})
    assert client.post(f"{BASE_URL}/login", data={"password": PASSWORD}).status_code == 303
    for _ in range(10):
        assert client.post(f"{BASE_URL}/login", data={"password": "wrong-password"}).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "https://images2pdf.test.evil.example"},
        {"Origin": "http://images2pdf.test"},
        {"Origin": "https://images2pdf.test:8443"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_origin_posts_are_rejected(logged_in, headers):
    resp = logged_in.post(f"{BASE_URL}/oauth/disconnect", headers=headers)
    assert resp.status_code == 403


def test_same_origin_post_is_allowed(logged_in):
    resp = logged_in.post(f"{BASE_URL}/logout", headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 303


def test_security_headers(logged_in):
    resp = logged_in.get(f"{BASE_URL}/")
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    static = logged_in.get(f"{BASE_URL}/static/style.css")
    assert static.headers["Cache-Control"] == "no-cache"  # כתובת קבועה → בלי מטמון ארוך
    assert static.headers.get("ETag")
