"""הסיסמה (שלב 4): כניסה ראשונה חד-פעמית, בחירה, החלפה, איפוס דרך WEB_PANEL_SECRET,
דיסק לא זמין, שמירה כ-hash, ונרמול עברית. הטפסים נשלחים כמו בדפדפן: מתוך ה-HTML שנוצר."""

from __future__ import annotations

import json
import threading
import unicodedata
from html.parser import HTMLParser

import pytest
from helpers import BASE_URL, CHOSEN_PASSWORD, PASSWORD, seed_password, sign_in

from auth import COOKIE_NAME
from password_store import PasswordStore

NEW_PASSWORD = "סיסמה חדשה עם רווחים 7"


class _Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append({"action": a.get("action"), "method": (a.get("method") or "get").lower(), "inputs": []})
        elif tag == "input" and self.forms:
            self.forms[-1]["inputs"].append(a)


def _form(client, path: str) -> dict:
    page = client.get(f"{BASE_URL}{path}")
    assert page.status_code == 200, page.status_code
    parser = _Forms()
    parser.feed(page.get_data(as_text=True))
    forms = [f for f in parser.forms if any(i.get("type") == "password" for i in f["inputs"])]
    assert len(forms) == 1
    return forms[0]


def _submit(client, form: dict, **passwords):
    """ממלא את שדות הסיסמה לפי השם שלהם בטופס, ושולח את שאר השדות כמו שהם (POST)."""
    assert form["method"] == "post"
    data = {i["name"]: i.get("value", "") for i in form["inputs"] if i.get("type") != "password"}
    for i in form["inputs"]:
        if i.get("type") == "password":
            data[i["name"]] = passwords[i["name"]]
    return client.post(f"{BASE_URL}{form['action']}", data=data)


def _login_env(client):
    resp = client.post(f"{BASE_URL}/login", data={"password": PASSWORD})
    assert resp.status_code == 303 and resp.headers["Location"] == "/password/setup"
    return client


def _record(app) -> dict:
    return json.loads((app.extensions["images2pdf"].settings.data_dir / "password.json").read_text())


# ─── כניסה ראשונה ובחירת סיסמה ──────────────────────────────

def test_first_login_then_choosing_a_password_through_the_rendered_form(app, client):
    _login_env(client)
    form = _form(client, "/password/setup")
    assert form["action"] == "/password/setup"
    by_name = {i["name"]: i for i in form["inputs"]}
    assert by_name["new_password"]["autocomplete"] == "new-password"
    assert by_name["confirm_password"]["autocomplete"] == "new-password"
    assert by_name["username"]["autocomplete"] == "username" and "hidden" in by_name["username"]

    resp = _submit(client, form, new_password=CHOSEN_PASSWORD, confirm_password=CHOSEN_PASSWORD)
    assert resp.status_code == 303 and resp.headers["Location"] == "/?password=set"
    assert client.get(f"{BASE_URL}/settings").status_code == 200  # המכשיר הזה כבר בפנים

    other = app.test_client()
    assert other.post(f"{BASE_URL}/login", data={"password": PASSWORD}).status_code == 401  # החד-פעמית נגמרה
    assert other.post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 303


@pytest.mark.parametrize(
    "new, confirm, message",
    [
        ("1234567", "1234567", "לפחות 8 תווים"),
        ("סיסמה-אחת-1", "סיסמה-אחת-2", "לא זהות"),
        ("א" * 257, "א" * 257, "עד 256 תווים"),
        ("", "", "צריך להקליד"),
    ],
)
def test_bad_new_password_is_refused_with_a_reason(app, client, new, confirm, message):
    _login_env(client)
    resp = client.post(f"{BASE_URL}/password/setup", data={"new_password": new, "confirm_password": confirm})
    assert resp.status_code == 400 and message in resp.get_data(as_text=True)
    assert app.extensions["images2pdf"].password_store.current() is None


def test_eight_hebrew_characters_are_enough(app, client):
    _login_env(client)
    resp = client.post(f"{BASE_URL}/password/setup", data={"new_password": "אבגדהוזח", "confirm_password": "אבגדהוזח"})
    assert resp.status_code == 303
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": "אבגדהוזח"}).status_code == 303


def test_setup_page_is_only_for_the_one_time_state(logged_in):
    resp = logged_in.get(f"{BASE_URL}/password/setup")
    assert resp.status_code == 302 and resp.headers["Location"] == "/"


def test_logout_works_before_choosing(client):
    _login_env(client)
    assert client.post(f"{BASE_URL}/logout").status_code == 303
    assert client.get(f"{BASE_URL}/password/setup").status_code == 302


def test_two_devices_choosing_at_once_only_one_wins(app):
    """בדיקת מקביליות אמיתית: שני מכשירים שנכנסו עם הסיסמה החד-פעמית בוחרים באותו רגע."""
    clients = [_login_env(app.test_client()) for _ in range(2)]
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def choose(i):
        barrier.wait()
        pw = f"{CHOSEN_PASSWORD}-{i}"
        results[i] = (clients[i].post(f"{BASE_URL}/password/setup", data={"new_password": pw, "confirm_password": pw}).status_code, pw)

    threads = [threading.Thread(target=choose, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(code for code, _ in results) == [303, 409]
    winner = next(pw for code, pw in results if code == 303)
    loser = next(pw for code, pw in results if code == 409)
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": winner}).status_code == 303
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": loser}).status_code == 401


def test_a_second_device_logged_in_with_the_one_time_password_is_out_after_the_choice(app):
    first, second = _login_env(app.test_client()), _login_env(app.test_client())
    first.post(f"{BASE_URL}/password/setup", data={"new_password": CHOSEN_PASSWORD, "confirm_password": CHOSEN_PASSWORD})
    resp = second.get(f"{BASE_URL}/settings")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login")


# ─── החלפת סיסמה ─────────────────────────────────────────────

def test_change_password_keeps_this_device_and_signs_out_the_others(app):
    here = sign_in(app, app.test_client())
    elsewhere = sign_in(app, app.test_client())
    form = _form(here, "/password")
    by_name = {i["name"]: i for i in form["inputs"]}
    assert by_name["current_password"]["autocomplete"] == "current-password"
    assert by_name["new_password"]["autocomplete"] == "new-password"
    assert by_name["username"]["autocomplete"] == "username"

    resp = _submit(here, form, current_password=CHOSEN_PASSWORD, new_password=NEW_PASSWORD, confirm_password=NEW_PASSWORD)
    assert resp.status_code == 200 and "הסיסמה הוחלפה" in resp.get_data(as_text=True)
    assert COOKIE_NAME in resp.headers["Set-Cookie"]
    assert here.get(f"{BASE_URL}/settings").status_code == 200
    assert elsewhere.get(f"{BASE_URL}/settings").status_code == 302

    assert app.test_client().post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 401
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": NEW_PASSWORD}).status_code == 303


def test_wrong_current_password_is_refused(app, logged_in):
    resp = logged_in.post(f"{BASE_URL}/password", data={
        "current_password": "לא-הסיסמה", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert resp.status_code == 401 and "הסיסמה הנוכחית שגויה" in resp.get_data(as_text=True)
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 303


def test_current_password_check_shares_the_login_limit(app, logged_in):
    """9 כניסות שגויות + ניסיון שגוי אחד בטופס ההחלפה = 10, והכניסה הבאה חסומה."""
    for _ in range(9):
        assert app.test_client().post(f"{BASE_URL}/login", data={"password": "wrong-password"}).status_code == 401
    bad = {"current_password": "wrong-password", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
    assert logged_in.post(f"{BASE_URL}/password", data=bad).status_code == 401
    assert logged_in.post(f"{BASE_URL}/password", data=bad).status_code == 429
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 429


# ─── איפוס דרך WEB_PANEL_SECRET ──────────────────────────────

def test_changing_the_env_secret_resets_to_a_new_one_time_password(make_app):
    first = make_app()
    old_cookie_client = sign_in(first, first.test_client())

    reset = make_app(WEB_PANEL_SECRET="סיסמה-זמנית-חדשה-99")  # אמיר החליף את הערך ב-Render
    c = reset.test_client()
    assert c.post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 401  # הסיסמה השמורה בוטלה
    assert c.post(f"{BASE_URL}/login", data={"password": PASSWORD}).status_code == 401  # גם הערך הקודם
    resp = c.post(f"{BASE_URL}/login", data={"password": "סיסמה-זמנית-חדשה-99"})
    assert resp.status_code == 303 and resp.headers["Location"] == "/password/setup"

    # עוגייה מהתקופה שלפני האיפוס לא פותחת את הכלי
    old_cookie = old_cookie_client.get_cookie(COOKIE_NAME, domain="images2pdf.test").value
    other = reset.test_client()
    other.set_cookie(COOKIE_NAME, old_cookie, domain="images2pdf.test")
    assert other.get(f"{BASE_URL}/settings").status_code == 302


def test_without_a_reset_the_chosen_password_survives_a_restart(make_app):
    first = make_app()
    seed_password(first)
    again = make_app()  # worker חדש / פריסה חדשה, אותו ערך ב-WEB_PANEL_SECRET
    assert again.test_client().post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 303
    assert again.test_client().post(f"{BASE_URL}/login", data={"password": PASSWORD}).status_code == 401


# ─── דיסק לא זמין: חוסמים, ולא חוזרים לסיסמה מה-env ──────────

def test_unusable_disk_blocks_login_instead_of_falling_back(make_app, tmp_path):
    not_a_dir = tmp_path / "data-is-a-file"
    not_a_dir.write_text("oops")
    app = make_app(DATA_DIR=str(not_a_dir))
    c = app.test_client()
    assert c.get(f"{BASE_URL}/login").status_code == 503
    resp = c.post(f"{BASE_URL}/login", data={"password": PASSWORD})
    assert resp.status_code == 503 and "אי אפשר להיכנס כרגע" in resp.get_data(as_text=True)
    assert COOKIE_NAME not in resp.headers.get("Set-Cookie", "")
    assert c.get(f"{BASE_URL}/privacy").status_code == 200  # הדף הציבורי לא תלוי בדיסק


def test_missing_disk_mount_blocks_login(make_app, tmp_path):
    """ב-Render, דיסק שלא חובר נראה כתיקייה שלא קיימת. זה לא "עוד לא נבחרה סיסמה"."""
    app = make_app(DATA_DIR=str(tmp_path / "not-mounted"))
    resp = app.test_client().post(f"{BASE_URL}/login", data={"password": PASSWORD})
    assert resp.status_code == 503 and COOKIE_NAME not in resp.headers.get("Set-Cookie", "")


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'{"v": 1, "kdf": "md5"}', b'{"v": 1}'])
def test_damaged_password_file_blocks_login(app, client, content):
    seed_password(app)
    (app.extensions["images2pdf"].settings.data_dir / "password.json").write_bytes(content)
    for password in (PASSWORD, CHOSEN_PASSWORD):
        resp = client.post(f"{BASE_URL}/login", data={"password": password})
        assert resp.status_code == 503, password


def test_damaged_file_also_locks_out_existing_sessions(app, logged_in):
    (app.extensions["images2pdf"].settings.data_dir / "password.json").write_bytes(b"{broken")
    assert logged_in.get(f"{BASE_URL}/settings").status_code == 503
    api = logged_in.get(f"{BASE_URL}/api/drive/search?q=abc")
    assert api.status_code == 503 and api.get_json() == {"error": "storage_unavailable"}


# ─── שמירה ─────────────────────────────────────────────────────

def test_stored_as_salted_hash_not_text(app):
    seed_password(app)
    raw = (app.extensions["images2pdf"].settings.data_dir / "password.json").read_bytes()
    assert CHOSEN_PASSWORD.encode("utf-8") not in raw and PASSWORD.encode("utf-8") not in raw
    rec = _record(app)
    assert rec["kdf"] == "scrypt" and (rec["n"], rec["r"], rec["p"]) == (2**14, 8, 5)
    assert len(rec["salt"]) >= 24  # base64 של 16 בתים


def test_same_password_gives_a_different_hash_each_time(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    PasswordStore(a, PASSWORD).set_initial(CHOSEN_PASSWORD)
    PasswordStore(b, PASSWORD).set_initial(CHOSEN_PASSWORD)
    ra, rb = (json.loads((d / "password.json").read_text()) for d in (a, b))
    assert ra["salt"] != rb["salt"] and ra["hash"] != rb["hash"]


@pytest.mark.parametrize(
    "typed_at_setup, typed_at_login",
    [
        ("café-עולם-12", "cafe\u0301-עולם-12"),       # é כתו אחד, או e ואחריו סימן נפרד (כך macOS שומר שמות)
        ("\ufb2aלום-עולם-12", "\u05e9\u05c1לום-עולם-12"),  # שׁ בצורת תצוגה (U+FB2A), או ש ואחריה נקודה
    ],
)
def test_same_password_in_another_unicode_form_still_matches(app, typed_at_setup, typed_at_login):
    """מקלדות שונות שולחות לפעמים אותה אות כרצף תווים אחר. אחרי נרמול NFC הן זהות."""
    assert typed_at_setup != typed_at_login
    assert unicodedata.normalize("NFC", typed_at_setup) == unicodedata.normalize("NFC", typed_at_login)
    app.extensions["images2pdf"].password_store.set_initial(typed_at_setup)
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": typed_at_login}).status_code == 303


# ─── לא בכתובת ────────────────────────────────────────────────

def test_passwords_in_the_url_are_ignored(app, client):
    seed_password(app)
    resp = client.post(f"{BASE_URL}/login?password={CHOSEN_PASSWORD}", data={})
    assert resp.status_code == 400 and COOKIE_NAME not in resp.headers.get("Set-Cookie", "")
    signed = sign_in(app, app.test_client())
    resp = signed.post(f"{BASE_URL}/password?current_password={CHOSEN_PASSWORD}&new_password={NEW_PASSWORD}"
                       f"&confirm_password={NEW_PASSWORD}", data={})
    assert resp.status_code == 400
    assert app.test_client().post(f"{BASE_URL}/login", data={"password": CHOSEN_PASSWORD}).status_code == 303


def test_password_forms_post_to_themselves(app, client):
    _login_env(client)
    assert _form(client, "/password/setup")["method"] == "post"
    signed = sign_in(app, app.test_client())
    assert _form(signed, "/password")["method"] == "post"
    assert _form(app.test_client(), "/login")["method"] == "post"
