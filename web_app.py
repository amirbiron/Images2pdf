"""
web_app.py — אפליקציית Flask של כלי ה-PDF ללוקיישנים.

כניסה עם סיסמה, חיבור לגוגל בדפדפן, הבורר ויצירת PDF.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from flask import Flask, current_app, g, jsonify, redirect, render_template, request
from google.auth.exceptions import RefreshError

import auth
import oauth
import pdf_jobs
import picker_api
import thumbnails
from config import Settings, load_settings
from google_api import GoogleNotConnected, forget_cached_clients, refresh_error_kind
from login_limiter import LoginLimiter
from pdf_builder import TITLE_MAX_CHARS
from pdf_job import MAX_IMAGES
from oauth_state import OAuthStateStore
from password_store import PasswordStorageError, PasswordStore
from storage import data_dir_problem
from token_store import TokenStore, TokenUnreadable

logger = logging.getLogger("images2pdf")

_CSP = "; ".join(
    [
        "default-src 'self'",
        # התמונות הממוזערות של גוגל מגיעות מ-googleusercontent.com: בבדיקה 5 של שלב 0 הן
        # נטענו ישירות וגם דרך השרת, והשרת שולח את הבקשה רק למארח הזה (is_allowed_thumbnail_url).
        # תמונה שנחסמת בכל זאת עוברת בדפדפן לנתיב שדרך השרת ('self').
        "img-src 'self' data: https://googleusercontent.com https://*.googleusercontent.com",
        "script-src 'self'",
        "style-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]
)


@dataclass
class Services:
    settings: Settings
    token_store: TokenStore | None
    state_store: OAuthStateStore
    login_limiter: LoginLimiter
    password_store: PasswordStore | None


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
        )


def create_app(settings: Settings | None = None) -> Flask:
    _configure_logging()
    settings = settings or load_settings()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # טפסים ודיווח JSON קטנים בלבד

    disk = data_dir_problem(settings.data_dir)
    if disk:
        logger.warning("Data directory is not usable: %s", settings.data_dir)
    token_store = TokenStore(settings.data_dir, settings.token_key) if settings.token_key else None
    app.extensions["images2pdf"] = Services(
        settings=settings,
        token_store=token_store,
        state_store=OAuthStateStore(settings.data_dir),
        login_limiter=LoginLimiter(settings.data_dir),
        # בלי WEB_PANEL_SECRET השירות חסום ממילא (auth.gate), ואין ממה לגזור סיסמה חד-פעמית.
        password_store=PasswordStore(settings.data_dir, settings.panel_secret) if settings.panel_secret else None,
    )

    # רישום איפוס של WEB_PANEL_SECRET מיד עם העלייה (Render מעלה את השירות מחדש אחרי כל
    # שמירה של משתנה). כשל לא מפיל את העלייה: הכניסה עצמה תיחסם ותציג הודעה.
    if app.extensions["images2pdf"].password_store is not None and disk is None:
        try:
            app.extensions["images2pdf"].password_store.current()
        except PasswordStorageError:
            logger.error("Password storage is not usable at startup; login stays blocked until it is fixed")

    app.before_request(auth.gate)
    app.before_request(_check_origin)
    app.after_request(_security_headers)

    app.register_blueprint(auth.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(thumbnails.bp)
    app.register_blueprint(picker_api.bp)
    app.register_blueprint(pdf_jobs.bp)

    app.add_url_rule("/healthz", "healthz", healthz)
    # / הוא דף בית ציבורי (גוגל דורש דף בית בלי כניסה, עם שם האפליקציה), והכלי ב-/app.
    app.add_url_rule("/", "home", home_page)
    app.add_url_rule(auth.APP_PATH, "index", index)
    app.add_url_rule("/settings", "settings", settings_page)
    app.add_url_rule("/help", "help", help_page)
    # ציבורי (auth.PUBLIC_ENDPOINTS): גוגל דורשת קישור למדיניות פרטיות שפתוח לכולם.
    app.add_url_rule("/privacy", "privacy", privacy_page)

    app.register_error_handler(RefreshError, _on_refresh_error)
    app.register_error_handler(GoogleNotConnected, _on_not_connected)
    app.register_error_handler(500, _on_server_error)
    return app


# ─── שכבות שעוטפות כל בקשה ────────────────────────────────────

def _expected_origin() -> str:
    settings = current_app.extensions["images2pdf"].settings
    if settings.public_base_url:
        return settings.public_base_url
    return request.host_url.rstrip("/")


def _check_origin():
    """הגנת CSRF נוספת על SameSite=Lax: POST חייב להגיע מאותו origin.

    משווים scheme + host + פורט במלואם, לא קידומת. בלי כותרת Origin נשענים על
    Sec-Fetch-Site; בלי שתיהן (לקוח שאינו דפדפן) — העוגייה עדיין נדרשת.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin is not None and origin != "null":
        if origin.rstrip("/") != _expected_origin():
            logger.warning("Rejected cross-origin %s to %s", request.method, request.path)
            return _forbidden()
        return None
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or origin == "null":
        logger.warning("Rejected cross-site %s to %s", request.method, request.path)
        return _forbidden()
    return None


def _forbidden():
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "forbidden"}), 403
    return render_template("message.html", title="הבקשה נחסמה", message="הבקשה לא הגיעה מהעמוד של הכלי."), 403


def _security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if current_app.extensions["images2pdf"].settings.production:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    # קבצים סטטיים: Flask שולח בלי max-age, כלומר הדפדפן בודק מול ETag בכל טעינה —
    # הכתובות שלהם קבועות, ולכן אסור מטמון ארוך. כל השאר: לא נשמר במטמון בכלל.
    if request.endpoint != "static" and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ─── דפים ─────────────────────────────────────────────────────

def healthz():
    # ציבורי ותמיד 200 כשהתהליך חי: גם שירות שעוד לא הוגדר צריך לעבור deploy
    # כדי שאפשר יהיה להיכנס ולראות מה חסר.
    return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


def _connection_status() -> tuple[dict, list[str]]:
    """מצב החיבור לגוגל, ורשימת ההגדרות שחסרות (בעברית)."""
    services = current_app.extensions["images2pdf"]
    settings = services.settings
    problems = list(settings.google_problems)
    disk = data_dir_problem(settings.data_dir)
    if disk:
        problems.append(disk)
    status = {"state": "not_configured"}
    # כשהדיסק לא תקין לא נוגעים בקובץ ההרשאה (stat עלול לזרוק NotADirectoryError).
    if services.token_store is not None and disk is None:
        try:
            grant = services.token_store.load()
        except TokenUnreadable:
            status = {"state": "unreadable"}
        except OSError:
            logger.warning("Could not read the stored grant", exc_info=True)
            status = {"state": "unreadable"}
        else:
            if grant is None:
                status = {"state": "disconnected"}
            else:
                status = {
                    "state": "connected",
                    "email": oauth.mask_email(grant.email),
                    "connected_at": grant.connected_at,
                    "time_limited": grant.refresh_token_expires_in is not None,
                }
    return status, problems


def _settings_response(status: dict, problems: list[str]):
    return render_template(
        "settings.html",
        status=status,
        problems=problems,
        redirect_uri=oauth.redirect_uri(),
        just_connected=request.args.get("connected") == "1",
        just_disconnected=request.args.get("disconnected") == "1",
        password_set=request.args.get("password") == "set",
    )


def home_page():
    """דף הבית הציבורי: מה הכלי עושה, וקישורים לכניסה ולמדיניות הפרטיות.

    מי שכבר מחובר ממשיך ישר לכלי, כדי שסימניות וקיצורי דרך ישנים ל-/ ימשיכו לעבוד.
    הבודקים של גוגל לא מחוברים, ולכן רואים את הדף. הדף לא תלוי בדיסק או בהגדרות:
    is_authenticated מחזיר False כשאי אפשר לקרוא את הסיסמה, והדף מוצג כרגיל.
    """
    if auth.is_authenticated():
        return redirect(auth.APP_PATH, code=302)
    settings = current_app.extensions["images2pdf"].settings
    return render_template(
        "landing.html",
        site_verification=settings.google_site_verification,
        show_nav=False,
    )


def index():
    """הכלי: חיפוש לוקיישן (ROADMAP), ב-/app. בלי חיבור לגוגל — דף החיבור."""
    status, problems = _connection_status()
    if not problems and status["state"] == "connected":
        return render_template("picker.html", max_images=MAX_IMAGES, title_max=TITLE_MAX_CHARS,
                               password_set=request.args.get("password") == "set")
    return _settings_response(status, problems)


def help_page():
    return render_template("help.html")


def privacy_page():
    settings = current_app.extensions["images2pdf"].settings
    return render_template(
        "privacy.html",
        contact=settings.privacy_contact_email,
        updated_he="נובמבר 2026",
        updated_en="November 2026",
        show_nav=False,
    )


def settings_page():
    """מצב החיבור לגוגל, חיבור וניתוק, וכתובת החזרה להגדרה ב-Google Cloud."""
    status, problems = _connection_status()
    return _settings_response(status, problems)


# ─── שגיאות ───────────────────────────────────────────────────

def _reconnect_response(title: str, message: str, status: int):
    if request.path.startswith("/api/"):
        return jsonify({"error": "google_reconnect_required"}), status
    return render_template("reconnect.html", title=title, message=message), status


def _on_refresh_error(exc: RefreshError):
    kind = refresh_error_kind(exc)
    services = current_app.extensions["images2pdf"]
    if kind == "invalid_grant":
        # ההרשאה מתה (בוטלה, פגה, או חצי שנה בלי שימוש). ניסיון חוזר עם אותו
        # טוקן יחזיר אותה תשובה לנצח, ולכן מנתקים במפורש ומבקשים חיבור מחדש.
        version = getattr(g, "google_grant_version", None)
        deleted = services.token_store.delete_if_version(version) if services.token_store else False
        forget_cached_clients()
        logger.warning("Google grant is no longer valid (invalid_grant); stored grant removed=%s", deleted)
        return _reconnect_response(
            "צריך לחבר מחדש את חשבון הגוגל",
            "ההרשאה שהכלי קיבל מגוגל כבר לא בתוקף. זה קורה כשמבטלים את הגישה, או אחרי חצי שנה בלי שימוש.",
            401,
        )
    if kind == "invalid_client":
        logger.error("Google rejected the OAuth client (check GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)")
        return (
            render_template(
                "message.html",
                title="פרטי הלקוח של גוגל לא תקינים",
                message="גוגל דחה את מזהה הלקוח או את הסוד שלו.",
                hint="בודקים ב-Render את GOOGLE_CLIENT_ID ו-GOOGLE_CLIENT_SECRET מול Google Cloud.",
            ),
            503,
        )
    logger.warning("Google token refresh failed (not invalid_grant); retryable=%s", exc.retryable)
    return (
        render_template(
            "message.html",
            title="אין כרגע תקשורת עם גוגל",
            message="לא הצלחנו לחדש את ההרשאה מול גוגל. ייתכן שזו תקלה זמנית.",
            hint="מנסים שוב בעוד דקה.",
        ),
        503,
    )


def _on_not_connected(exc: GoogleNotConnected):
    if exc.reason == "unreadable":
        return _reconnect_response(
            "צריך לחבר מחדש את חשבון הגוגל",
            "ההרשאה השמורה לא נקראת, כנראה כי מפתח ההצפנה (TOKEN_ENCRYPTION_KEY) הוחלף.",
            409,
        )
    if exc.reason == "not_configured":
        return (
            render_template("message.html", title="החיבור לגוגל לא הוגדר", message="חסרות הגדרות. הפרטים בדף הבית."),
            503,
        )
    return _reconnect_response("חשבון הגוגל לא מחובר", "כדי להמשיך צריך לחבר את חשבון הגוגל.", 409)


def _on_server_error(exc):
    # Flask כבר רשם את החריגה (כולל traceback) ללוג. למשתמש — הודעה גנרית בעברית.
    if request.path.startswith("/api/"):
        return jsonify({"error": "server_error"}), 500
    return render_template("message.html", title="משהו השתבש", message="אירעה שגיאה בשרת. הפרטים נרשמו בלוג."), 500
