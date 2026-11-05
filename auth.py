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
  הנתיב: `/static/../dev/checks` לא יכול "להתחפש" לקובץ סטטי.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import quote, urlsplit

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

COOKIE_NAME = "pdf_panel_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
PASSWORD_FIELD = "password"

# endpoints שנגישים בלי כניסה. כל השאר — מאחורי הסיסמה, כולל נתיבי החיבור לגוגל.
PUBLIC_ENDPOINTS = frozenset({"static", "healthz", "auth.login"})


def _cookie_token(secret: str) -> str:
    # העוגייה מכילה HMAC של הסוד ולא את הסוד עצמו. החלפת הסיסמה מבטלת עוגיות קיימות.
    return hmac.new(secret.encode("utf-8"), b"images2pdf/auth-cookie/v1", hashlib.sha256).hexdigest()


def _passwords_match(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def is_authenticated() -> bool:
    settings = current_app.extensions["images2pdf"].settings
    if settings.panel_secret is None:
        return False
    cookie = request.cookies.get(COOKIE_NAME)
    if not isinstance(cookie, str) or not cookie:
        return False
    return hmac.compare_digest(cookie.encode("utf-8"), _cookie_token(settings.panel_secret).encode("utf-8"))


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
    if is_authenticated():
        return None
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


@bp.route("/login", methods=["GET", "POST"])
def login():
    services = current_app.extensions["images2pdf"]
    settings = services.settings
    if settings.panel_secret is None:
        return _misconfigured_response()

    nxt = safe_next(request.values.get("next"))
    if request.method == "GET":
        if is_authenticated():
            return redirect(nxt, code=302)
        return render_template("login.html", next=nxt, error=None, show_nav=False)

    password = request.form.get(PASSWORD_FIELD)
    if not isinstance(password, str) or password == "":
        return render_template("login.html", next=nxt, error="צריך להקליד סיסמה.", show_nav=False), 400
    if len(password) > 1024:
        # תקרה עם תשובה משלה, לא חיתוך: סיסמה חתוכה הייתה נבדקת כאילו היא מה שהוקלד.
        return render_template("login.html", next=nxt, error="הסיסמה ארוכה מדי.", show_nav=False), 400

    ticket, retry_after = services.login_limiter.try_acquire()
    if ticket is None:
        minutes = max(1, (retry_after + 59) // 60)
        resp = make_response(
            render_template(
                "login.html",
                next=nxt,
                error=f"יותר מדי ניסיונות כניסה. אפשר לנסות שוב בעוד {minutes} דקות.",
                show_nav=False,
            ),
            429,
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    if not _passwords_match(password, settings.panel_secret):
        logger.info("Failed login attempt")
        return render_template("login.html", next=nxt, error="הסיסמה שגויה.", show_nav=False), 401

    services.login_limiter.release_success(ticket)
    resp = make_response(redirect(nxt, code=303))
    resp.set_cookie(
        COOKIE_NAME,
        _cookie_token(settings.panel_secret),
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=settings.production,
        httponly=True,
        samesite="Lax",
    )
    logger.info("Successful login")
    return resp


@bp.post("/logout")
def logout():
    settings = current_app.extensions["images2pdf"].settings
    resp = make_response(redirect("/login", code=303))
    resp.delete_cookie(COOKIE_NAME, path="/", secure=settings.production, httponly=True, samesite="Lax")
    return resp
