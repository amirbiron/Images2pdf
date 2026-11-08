"""
auth.py — מסך הכניסה והשער שלפני כל נתיב.

ההבדלים מ-`_check_auth` / `_login_page` / `_set_auth_cookie` של הפאנל המקורי:
- הסיסמה נשלחת בטופס POST, ולא בכתובת כ-`?token=` (כתובת נשמרת בלוגים ובהיסטוריה).
- אין dev secret ואין `Authorization: Bearer`.
- בלי WEB_PANEL_SECRET השירות חוסם הכול (503), במקום לרוץ בלי אימות.
- ההשוואה היא על bytes: `hmac.compare_digest` זורק TypeError על מחרוזת עם
  תווים שאינם ASCII, כלומר על סיסמה בעברית.
- בפרודקשן העוגייה תמיד Secure, בלי תלות ב-X-Forwarded-Proto.
- ההחלטה מה ציבורי נעשית לפי ה-endpoint שנמצא בניתוב, ולא לפי קידומת של
  הנתיב: `/static/../settings` לא יכול "להתחפש" לקובץ סטטי.

שלב 4 (password_store.py): הסיסמה מ-WEB_PANEL_SECRET חד-פעמית. אחרי כניסה איתה העוגייה
מאפשרת רק את מסך בחירת הסיסמה. אחרי הבחירה העוגייה נגזרת ממפתח אקראי שנשמר עם הסיסמה,
והחלפת סיסמה מחליפה את המפתח — כך מכשירים אחרים מתנתקים. כל הסיסמאות נשלחות ב-POST בלבד
(request.form), ואף נתיב לא קורא סיסמה מהכתובת.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import quote, urlsplit

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request

from password_store import (
    PASSWORD_MAX_CHARS,
    PASSWORD_MIN_CHARS,
    PasswordAlreadySet,
    PasswordStorageError,
    WrongPassword,
    password_problem,
)

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

COOKIE_NAME = "pdf_panel_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
PASSWORD_FIELD = "password"
# שם משתמש קבוע בשדה נסתר: כך מנהל הסיסמאות של הדפדפן מזהה שזו אותה רשומה, ומעדכן
# אותה אחרי החלפת סיסמה. מקור: https://www.chromium.org/developers/design-documents/create-amazing-password-forms/
# ("the username field in a change password form ... include a hidden input field").
USERNAME = "images2pdf"
FORM_PASSWORD_MAX_CHARS = 1024  # תקרת קלט בטופס; הסיסמה החדשה עצמה מוגבלת ל-PASSWORD_MAX_CHARS

# endpoints שנגישים בלי כניסה. כל השאר — מאחורי הסיסמה, כולל נתיבי החיבור לגוגל.
# "privacy" הוא בדיוק /privacy (דף הפרטיות שגוגל דורשת), ולא כל נתיב שמתחיל כך.
PUBLIC_ENDPOINTS = frozenset({"static", "healthz", "auth.login", "privacy"})
# אחרי כניסה עם הסיסמה החד-פעמית: רק בחירת סיסמה ויציאה.
SETUP_ENDPOINTS = frozenset({"auth.password_setup", "auth.logout"})


def _hmac(key: bytes, label: bytes) -> str:
    return hmac.new(key, label, hashlib.sha256).hexdigest()


def full_cookie_value(session_key: bytes) -> str:
    """העוגייה של מי שנכנס עם הסיסמה השמורה. נגזרת מהמפתח שמתחלף בכל החלפת סיסמה."""
    return _hmac(session_key, b"images2pdf/auth-cookie/v2")


def _setup_cookie_value(env_secret: str) -> str:
    """העוגייה אחרי כניסה עם הסיסמה החד-פעמית: פותחת רק את בחירת הסיסמה. מתבטלת מעצמה
    כשנבחרת סיסמה (המצב כבר לא setup) או כשהערך של WEB_PANEL_SECRET מוחלף."""
    return _hmac(env_secret.encode("utf-8"), b"images2pdf/setup-cookie/v2")


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _store():
    return current_app.extensions["images2pdf"].password_store


def access_level() -> str | None:
    """"full" / "setup" / None לפי העוגייה. זורק PasswordStorageError כשאי אפשר לדעת."""
    settings = current_app.extensions["images2pdf"].settings
    if settings.panel_secret is None:
        return None
    cookie = request.cookies.get(COOKIE_NAME)
    stored = _store().current()  # גם בלי עוגייה: דיסק לא זמין חוסם את כל הדפים
    if not isinstance(cookie, str) or not cookie:
        return None
    if stored is not None:
        return "full" if _same(cookie, full_cookie_value(stored.session_key)) else None
    return "setup" if _same(cookie, _setup_cookie_value(settings.panel_secret)) else None


def is_authenticated() -> bool:
    try:
        return access_level() == "full"
    except PasswordStorageError:
        # "לא מחובר" כשאי אפשר לקרוא את הסיסמה: נכשל-סגור. הכניסה עצמה מוצגת כחסומה
        # ב-login וב-gate (_storage_blocked_response), ולא כטופס כניסה רגיל.
        return False


def _set_cookie(resp, value: str) -> None:
    settings = current_app.extensions["images2pdf"].settings
    resp.set_cookie(COOKIE_NAME, value, max_age=COOKIE_MAX_AGE, path="/", secure=settings.production,
                    httponly=True, samesite="Lax")


def _clear_cookie(resp) -> None:
    settings = current_app.extensions["images2pdf"].settings
    resp.delete_cookie(COOKIE_NAME, path="/", secure=settings.production, httponly=True, samesite="Lax")


def safe_next(target: object) -> str:
    """יעד אחרי כניסה: רק נתיב מקומי. כל דבר אחר → `/`.

    בודקים מבנה ולא קידומת: `//evil.com` ו-`/\\evil.com` מתחילים ב-`/` אבל
    הדפדפן מפרש אותם ככתובת חיצונית.
    """
    if not isinstance(target, str) or not target.startswith("/") or len(target) > 2000:
        return "/"
    if target.startswith("//") or "\\" in target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target


def gate():
    """רץ לפני כל בקשה (before_request)."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    settings = current_app.extensions["images2pdf"].settings
    if settings.panel_secret is None:
        return _misconfigured_response()
    try:
        level = access_level()
    except PasswordStorageError:
        return _storage_blocked_response()
    if level == "full":
        if request.endpoint == "auth.password_setup":
            return redirect("/", code=302)
        return None
    if level == "setup":
        if request.endpoint in SETUP_ENDPOINTS:
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "password_setup_required"}), 403
        return redirect("/password/setup", code=302 if request.method == "GET" else 303)
    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        nxt = request.full_path if request.query_string else request.path
        return redirect(f"/login?next={_quote(nxt)}", code=302)
    return redirect("/login", code=303)


def _quote(value: str) -> str:
    return quote(value, safe="/")


def _misconfigured_response():
    settings = current_app.extensions["images2pdf"].settings
    if request.path.startswith("/api/"):
        return jsonify({"error": "service_not_configured"}), 503
    return (
        render_template(
            "message.html",
            title="השירות עדיין לא הוגדר",
            message=settings.panel_secret_problem,
            hint="מגדירים את הסיסמה ב-Render, בעמוד Environment של השירות. אחרי השמירה השירות עולה מחדש.",
            show_nav=False,
        ),
        503,
    )


def _storage_blocked_response():
    """הדיסק לא זמין או שקובץ הסיסמה פגום: חוסמים, ולא חוזרים לסיסמה מ-WEB_PANEL_SECRET."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "storage_unavailable"}), 503
    return (
        render_template(
            "message.html",
            title="אי אפשר להיכנס כרגע",
            message="הכלי לא מצליח לקרוא את הסיסמה השמורה בשרת, ולכן הכניסה חסומה.",
            hint="זו תקלה בשרת ולא בסיסמה. פונים למי שהקים את הכלי.",
            show_nav=False,
        ),
        503,
    )


def _limited(template: str, status_text: str, **context):
    """בודק את מכסת הניסיונות. מחזיר (ticket, None) או (None, תשובת 429)."""
    services = current_app.extensions["images2pdf"]
    ticket, retry_after = services.login_limiter.try_acquire()
    if ticket is not None:
        return ticket, None
    minutes = max(1, (retry_after + 59) // 60)
    context = {**context, "error": f"{status_text} אפשר לנסות שוב בעוד {minutes} דקות."}
    resp = make_response(render_template(template, **context), 429)
    resp.headers["Retry-After"] = str(retry_after)
    return None, resp


@bp.route("/login", methods=["GET", "POST"])
def login():
    services = current_app.extensions["images2pdf"]
    settings = services.settings
    if settings.panel_secret is None:
        return _misconfigured_response()

    nxt = safe_next(request.values.get("next"))
    page = {"next": nxt, "username": USERNAME, "show_nav": False}
    try:
        stored = _store().current()
    except PasswordStorageError:
        return _storage_blocked_response()

    if request.method == "GET":
        if is_authenticated():
            return redirect(nxt, code=302)
        return render_template("login.html", error=None, **page)

    password = request.form.get(PASSWORD_FIELD)
    if not isinstance(password, str) or password == "":
        return render_template("login.html", error="צריך להקליד סיסמה.", **page), 400
    if len(password) > FORM_PASSWORD_MAX_CHARS:
        # תקרה עם תשובה משלה, לא חיתוך: סיסמה חתוכה הייתה נבדקת כאילו היא מה שהוקלד.
        return render_template("login.html", error="הסיסמה ארוכה מדי.", **page), 400

    ticket, limited = _limited("login.html", "יותר מדי ניסיונות כניסה.", **page)
    if limited:
        return limited

    if stored is not None:
        if not _store().verify(stored, password):
            logger.info("Failed login attempt")
            return render_template("login.html", error="הסיסמה שגויה.", **page), 401
        services.login_limiter.release_success(ticket)
        resp = make_response(redirect(nxt, code=303))
        _set_cookie(resp, full_cookie_value(stored.session_key))
        logger.info("Successful login")
        return resp

    # אין סיסמה שמורה: הסיסמה מ-WEB_PANEL_SECRET היא חד-פעמית, ומובילה לבחירת סיסמה.
    if not _store().verify_one_time(password):
        logger.info("Failed login attempt")
        return render_template("login.html", error="הסיסמה שגויה.", **page), 401
    services.login_limiter.release_success(ticket)
    resp = make_response(redirect("/password/setup", code=303))
    _set_cookie(resp, _setup_cookie_value(settings.panel_secret))
    logger.info("Login with the one-time password; a new password must be chosen")
    return resp


def _new_password_error() -> str | None:
    new = request.form.get("new_password")
    confirm = request.form.get("confirm_password")
    if not isinstance(new, str) or not isinstance(confirm, str) or new == "":
        return "צריך להקליד סיסמה חדשה פעמיים."
    if len(new) > FORM_PASSWORD_MAX_CHARS or len(confirm) > FORM_PASSWORD_MAX_CHARS:
        return f"הסיסמה יכולה להכיל עד {PASSWORD_MAX_CHARS} תווים."
    problem = password_problem(new)
    if problem:
        return problem
    if new != confirm:
        return "שתי הסיסמאות שהוקלדו לא זהות."
    return None


def _password_page(**context):
    return {"username": USERNAME, "min_chars": PASSWORD_MIN_CHARS, "max_chars": PASSWORD_MAX_CHARS, **context}


@bp.route("/password/setup", methods=["GET", "POST"])
def password_setup():
    """בחירת סיסמה אחרי כניסה עם הסיסמה החד-פעמית. ה-gate מאפשר להגיע לכאן רק במצב הזה."""
    ctx = _password_page(show_nav=False, error=None)
    if request.method == "GET":
        return render_template("password_setup.html", **ctx)
    error = _new_password_error()
    if error:
        return render_template("password_setup.html", **{**ctx, "error": error}), 400
    try:
        stored = _store().set_initial(request.form["new_password"])
    except PasswordAlreadySet:
        resp = make_response(render_template(
            "message.html", title="כבר נבחרה סיסמה",
            message="מישהו כבר בחר סיסמה, ולכן הסיסמה החד-פעמית לא בתוקף יותר. נכנסים עם הסיסמה שנבחרה.",
            show_nav=False,
        ), 409)
        _clear_cookie(resp)
        return resp
    except PasswordStorageError:
        return _storage_blocked_response()
    resp = make_response(redirect("/?password=set", code=303))
    _set_cookie(resp, full_cookie_value(stored.session_key))
    return resp


@bp.route("/password", methods=["GET", "POST"])
def password_change():
    """החלפת סיסמה, עם הסיסמה הנוכחית. המכשיר הזה נשאר מחובר, ושאר המכשירים מתנתקים."""
    ctx = _password_page(error=None, done=False)
    if request.method == "GET":
        return render_template("password_change.html", **{**ctx, "done": request.args.get("changed") == "1"})
    current = request.form.get("current_password")
    if not isinstance(current, str) or current == "":
        return render_template("password_change.html", **{**ctx, "error": "צריך להקליד את הסיסמה הנוכחית."}), 400
    if len(current) > FORM_PASSWORD_MAX_CHARS:
        return render_template("password_change.html", **{**ctx, "error": "הסיסמה הנוכחית שגויה."}), 400
    error = _new_password_error()
    if error:
        return render_template("password_change.html", **{**ctx, "error": error}), 400
    # אותה מכסת ניסיונות כמו בכניסה: אחרת הטופס הזה היה דרך לנחש את הסיסמה בלי הגבלה.
    ticket, limited = _limited("password_change.html", "יותר מדי ניסיונות.", **ctx)
    if limited:
        return limited
    try:
        stored = _store().change(current, request.form["new_password"])
    except WrongPassword:
        logger.info("Failed password change (wrong current password)")
        return render_template("password_change.html", **{**ctx, "error": "הסיסמה הנוכחית שגויה."}), 401
    except PasswordStorageError:
        return _storage_blocked_response()
    current_app.extensions["images2pdf"].login_limiter.release_success(ticket)
    # הפניה (POST ← redirect ← GET): רענון של דף ההצלחה לא שולח שוב את הסיסמה הישנה, שהייתה
    # נכשלת ונספרת כניסיון שגוי. כמו אחרי בחירת סיסמה.
    resp = make_response(redirect("/password?changed=1", code=303))
    _set_cookie(resp, full_cookie_value(stored.session_key))
    return resp


@bp.post("/logout")
def logout():
    resp = make_response(redirect("/login", code=303))
    _clear_cookie(resp)
    return resp
