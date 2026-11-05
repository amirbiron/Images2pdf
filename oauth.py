"""
oauth.py — חיבור חשבון הגוגל בדפדפן (OAuth 2.0 לשרת ווב).

הזרימה: כפתור "חיבור לגוגל" (POST) ← דף ההסכמה של גוגל ← חזרה ל-/oauth/callback
← החלפת הקוד בטוקנים ← בדיקה שכל ההרשאות ניתנו ← בדיקה ב-about.get שזה החשבון
המורשה ← שמירה מוצפנת. נתיבי החיבור יושבים מאחורי הכניסה עם הסיסמה.

`scripts/google_oauth.py` מהפאנל המקורי שימש רק כדוגמה: הוא דורש טרמינל ומדפיס
את הטוקן למסך. כאן אין טרמינל, והטוקן לא מוצג ולא נרשם בלוג באף שלב.

מקורות: https://developers.google.com/identity/protocols/oauth2/web-server
(פרמטרים, החלפת קוד, שדה scope בתשובה, ביטול ב-/revoke).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, redirect, render_template, request
from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import DRIVE_SCOPES, GOOGLE_AUTH_URL, GOOGLE_REVOKE_URL, GOOGLE_TOKEN_URL, OAUTH_CALLBACK_PATH
from google_api import forget_cached_clients
from storage import data_dir_problem
from token_store import StoredGrant, TokenUnreadable

logger = logging.getLogger(__name__)

bp = Blueprint("oauth", __name__)

SCOPE_LABELS = {
    "https://www.googleapis.com/auth/drive.readonly": "צפייה בכל הקבצים בדרייב",
    "https://www.googleapis.com/auth/drive.file": "יצירת קבצים חדשים ועריכת הקבצים שהכלי עצמו יצר",
}


class OAuthExchangeError(Exception):
    """החלפת הקוד בטוקנים נכשלה. ההודעה טכנית ונשארת בלוג."""


class RevokeError(Exception):
    """ביטול ההרשאה אצל גוגל נכשל."""


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    scopes: frozenset[str]
    refresh_token_expires_in: int | None

    def __repr__(self) -> str:
        return f"TokenResponse(scopes={sorted(self.scopes)!r}, has_refresh_token={self.refresh_token is not None})"


def mask_email(email: str) -> str:
    """a***@gmail.com — לתצוגה בלבד. לא נרשם בלוג בשום צורה."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def redirect_uri() -> str:
    settings = current_app.extensions["images2pdf"].settings
    base = settings.public_base_url
    if base is None:
        # רק בפיתוח (בפרודקשן חסרה כתובת = תקלת הגדרה שחוסמת את החיבור).
        base = request.host_url.rstrip("/")
    return base + OAUTH_CALLBACK_PATH


def _setup_problems() -> list[str]:
    services = current_app.extensions["images2pdf"]
    problems = list(services.settings.google_problems)
    disk = data_dir_problem(services.settings.data_dir)
    if disk:
        problems.append(disk)
    return problems


def _page(title: str, message: str, *, status: int, hint: str | None = None, items: list[str] | None = None):
    return render_template("message.html", title=title, message=message, hint=hint, items=items or []), status


@bp.post("/oauth/start")
def start():
    problems = _setup_problems()
    if problems:
        return _page("אי אפשר להתחבר לגוגל עדיין", "חסרות הגדרות:", status=503, items=problems)
    services = current_app.extensions["images2pdf"]
    state = services.state_store.create()
    params = {
        "client_id": services.settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(DRIVE_SCOPES),
        # offline: לקבל refresh token. prompt=consent: לקבל אותו גם בחיבור חוזר.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "login_hint": services.settings.allowed_google_email,
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", code=303)


@bp.get(OAUTH_CALLBACK_PATH)
def callback():
    services = current_app.extensions["images2pdf"]
    problems = _setup_problems()
    if problems:
        return _page("אי אפשר להתחבר לגוגל עדיין", "חסרות הגדרות:", status=503, items=problems)

    # קודם ה-state, ורק אחר כך כל השאר — גם הודעת שגיאה מגוגל.
    if not services.state_store.consume(request.args.get("state")):
        logger.warning("OAuth callback with an unknown, reused or expired state")
        return _page(
            "קישור החיבור לא בתוקף",
            "הקישור כבר שימש, פג תוקפו (10 דקות), או שלא נוצר כאן.",
            hint="חוזרים לדף הבית ולוחצים שוב על \"חיבור לגוגל\".",
            status=400,
        )

    error = request.args.get("error")
    if error is not None:
        logger.info("Google returned an authorization error: %s", error if isinstance(error, str) and len(error) < 64 else "<unexpected>")
        if error == "access_denied":
            return _page("החיבור בוטל", "לא אושרה גישה בדף של גוגל, ולכן לא נשמר כלום.", status=400)
        return _page("החיבור לגוגל נכשל", "גוגל החזיר שגיאה בזמן החיבור.", hint="אפשר לנסות שוב מדף הבית.", status=400)

    code = request.args.get("code")
    if not isinstance(code, str) or not 0 < len(code) <= 2048:
        return _page("החיבור לגוגל נכשל", "גוגל לא החזיר קוד חיבור תקין.", status=400)

    try:
        tokens = exchange_code(code)
    except OAuthExchangeError:
        logger.warning("OAuth code exchange failed", exc_info=True)
        return _page(
            "החיבור לגוגל נכשל",
            "לא הצלחנו להשלים את החיבור מול גוגל.",
            hint="אם זה חוזר, בודקים ב-Render ש-GOOGLE_CLIENT_ID ו-GOOGLE_CLIENT_SECRET נכונים.",
            status=502,
        )

    missing = [s for s in DRIVE_SCOPES if s not in tokens.scopes]
    if missing:
        _revoke_quietly(tokens.refresh_token or tokens.access_token, why="missing scopes")
        logger.warning("User did not grant all scopes; missing %d", len(missing))
        return _page(
            "חסרות הרשאות",
            "בדף של גוגל לא סומנו כל ההרשאות שהכלי צריך. החיבור לא נשמר.",
            hint="לוחצים שוב על \"חיבור לגוגל\" ומסמנים את כל התיבות.",
            items=[SCOPE_LABELS.get(s, s) for s in missing],
            status=400,
        )

    if not tokens.refresh_token:
        _revoke_quietly(tokens.access_token, why="no refresh token")
        logger.error("Token response had no refresh_token despite access_type=offline and prompt=consent")
        return _page("החיבור לגוגל נכשל", "גוגל לא החזיר הרשאה קבועה.", hint="אפשר לנסות שוב מדף הבית.", status=502)

    try:
        email = fetch_account_email(tokens.access_token)
    except (HttpError, GoogleAuthError, requests.RequestException, OSError, ValueError):
        logger.warning("Could not read the connected account (about.get)", exc_info=True)
        _revoke_quietly(tokens.refresh_token, why="account check failed")
        return _page("החיבור לגוגל נכשל", "לא הצלחנו לבדוק לאיזה חשבון התחברת. החיבור לא נשמר.", status=502)

    allowed = services.settings.allowed_google_email
    if email is None or email.strip().casefold() != allowed.strip().casefold():
        _revoke_quietly(tokens.refresh_token, why="wrong account")
        logger.warning("Connected Google account does not match ALLOWED_GOOGLE_EMAIL; grant revoked")
        shown = mask_email(email) if email else "לא ידוע"
        return _page(
            "התחברת עם חשבון אחר",
            f"החשבון שאושר ({shown}) אינו החשבון המורשה לכלי ({mask_email(allowed)}). החיבור לא נשמר וההרשאה בוטלה.",
            hint="לוחצים שוב על \"חיבור לגוגל\" ובוחרים את החשבון הנכון.",
            status=403,
        )

    grant = StoredGrant(
        refresh_token=tokens.refresh_token,
        email=email,
        scopes=tuple(sorted(tokens.scopes)),
        connected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        refresh_token_expires_in=tokens.refresh_token_expires_in,
    )
    try:
        services.token_store.save(grant)
    except OSError:
        logger.error("Could not save the Google grant to disk", exc_info=True)
        _revoke_quietly(tokens.refresh_token, why="save failed")
        return _page(
            "החיבור לא נשמר",
            "ההרשאה התקבלה, אבל השמירה לדיסק נכשלה, ולכן היא בוטלה.",
            hint="בודקים ב-Render שהדיסק הקבוע מחובר ב-/var/data.",
            status=500,
        )
    forget_cached_clients()
    logger.info("Google account connected (all scopes granted, account matches)")
    return redirect("/?connected=1", code=303)


@bp.post("/oauth/disconnect")
def disconnect():
    services = current_app.extensions["images2pdf"]
    store = services.token_store
    if store is None:
        return redirect("/", code=303)
    try:
        grant = store.load()
    except (TokenUnreadable, OSError):
        # מוחקים מקומית בכל מקרה; אי אפשר לבטל אצל גוגל טוקן שלא ניתן לקרוא.
        logger.warning("Stored grant unreadable during disconnect; deleting it locally", exc_info=True)
        grant = None
    revoke_failed = False
    if grant is not None:
        try:
            revoke_token(grant.refresh_token)
        except RevokeError:
            revoke_failed = True
            logger.warning("Revoking the grant at Google failed during disconnect", exc_info=True)
    store.delete()
    forget_cached_clients()
    if revoke_failed:
        return _page(
            "החיבור נמחק מהכלי, אבל לא בוטל אצל גוגל",
            "הכלי כבר לא משתמש בהרשאה. כדי לבטל אותה גם אצל גוגל, מסירים את האפליקציה בעמוד ההרשאות של חשבון הגוגל.",
            hint="https://myaccount.google.com/permissions",
            status=502,
        )
    return redirect("/?disconnected=1", code=303)


# ─── קריאות לגוגל ─────────────────────────────────────────────

def exchange_code(code: str) -> TokenResponse:
    settings = current_app.extensions["images2pdf"].settings
    try:
        resp = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        # רק שם הסוג: הודעת החריגה של requests כוללת כתובת, והגוף (עם הסוד) לא נכנס אליה,
        # אבל אין סיבה להסתמך על זה.
        raise OAuthExchangeError(f"token request failed: {type(exc).__name__}") from None

    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if resp.status_code != 200:
        error = payload.get("error") if isinstance(payload, dict) else None
        raise OAuthExchangeError(
            f"token endpoint returned {resp.status_code} ({error if isinstance(error, str) and len(error) < 64 else 'no error code'})"
        )
    if not isinstance(payload, dict):
        raise OAuthExchangeError("token endpoint returned a non-object body")

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    scope = payload.get("scope")
    rt_expires = payload.get("refresh_token_expires_in")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthExchangeError("token response has no access_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise OAuthExchangeError("token response has a non-string refresh_token")
    if not isinstance(scope, str):
        raise OAuthExchangeError("token response has no scope field")
    if not (isinstance(rt_expires, int) and not isinstance(rt_expires, bool) and math.isfinite(rt_expires) and rt_expires >= 0):
        rt_expires = None
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token or None,
        scopes=frozenset(scope.split()),
        refresh_token_expires_in=rt_expires,
    )


def fetch_account_email(access_token: str) -> str | None:
    """כתובת החשבון שחובר, דרך about.get. None אם גוגל לא החזיר כתובת.

    `fields` חובה ב-about.get, ו-drive.readonly מספיק לה. מקור:
    https://developers.google.com/workspace/drive/api/reference/rest/v3/about/get
    """
    credentials = Credentials(token=access_token)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    about = drive.about().get(fields="user(emailAddress)").execute()
    user = about.get("user") if isinstance(about, dict) else None
    email = user.get("emailAddress") if isinstance(user, dict) else None
    return email if isinstance(email, str) and "@" in email else None


def revoke_token(token: str) -> None:
    """מבטל את ההרשאה אצל גוגל. זורק RevokeError בכל כשל.

    הטוקן נשלח בגוף הבקשה ולא בכתובת: כתובת עם טוקן נכנסת להודעות חריגה
    וללוגים. גוף בקשה בפורמט form מופיע בדוגמאות Node.js ו-Ruby בעמוד:
    https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke
    """
    try:
        resp = requests.post(
            GOOGLE_REVOKE_URL,
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RevokeError(f"revoke request failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise RevokeError(f"revoke endpoint returned {resp.status_code}")


def _revoke_quietly(token: str, *, why: str) -> None:
    """ביטול במסלול שכבר נכשל. כשל בביטול נרשם בלוג ולא מסתיר את השגיאה המקורית:
    ההרשאה לא נשמרה בכל מקרה, כך שהכלי לא ישתמש בה."""
    try:
        revoke_token(token)
    except RevokeError:
        logger.warning("Could not revoke an unsaved grant (%s)", why, exc_info=True)

