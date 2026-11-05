"""
web_app.py — אפליקציית Flask של כלי ה-PDF ללוקיישנים.

שלב 0: כניסה עם סיסמה, חיבור לגוגל בדפדפן, והדף /dev/checks.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from flask import Flask, current_app, g, jsonify, render_template, request
from google.auth.exceptions import RefreshError

import auth
import dev_checks
import oauth
import sample_pdf
import thumbnails
from config import Settings, load_settings
from google_api import GoogleNotConnected, forget_cached_clients, refresh_error_kind
from login_limiter import LoginLimiter
from oauth_state import OAuthStateStore
from storage import data_dir_problem
from token_store import TokenStore, TokenUnreadable

logger = logging.getLogger("images2pdf")

_CSP = "; ".join(
    [
        "default-src 'self'",
        # https: ולא רשימת מארחים: בדיקה 5 מודדת אם קישורים ישירים של גוגל נטענים,
        # ומדיניות צרה מדי הייתה חוסמת אותם בעצמה ונותנת תוצאה שגויה. יצומצם בשלב 2.
        "img-src 'self' https: data:",
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
    )

    app.before_request(auth.gate)
    app.before_request(_check_origin)
    app.after_request(_security_headers)

    app.register_blueprint(auth.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(dev_checks.bp)
    app.register_blueprint(thumbnails.bp)
    app.register_blueprint(sample_pdf.bp)

    app.add_url_rule("/healthz", "healthz", healthz)
    app.add_url_rule("/", "index", index)

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


def index():
    services = current_app.extensions["images2pdf"]
    settings = services.settings
    status = {"state": "not_configured"}
    if services.token_store is not None:
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
    problems = list(settings.google_problems)
    disk = data_dir_problem(settings.data_dir)
    if disk:
        problems.append(disk)
    return render_template(
        "index.html",
        status=status,
        problems=problems,
        redirect_uri=oauth.redirect_uri(),
        just_connected=request.args.get("connected") == "1",
        just_disconnected=request.args.get("disconnected") == "1",
    )


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
