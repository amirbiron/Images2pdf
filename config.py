"""
config.py — הגדרות הכלי, נקראות ממשתני סביבה.

ההבדל מ-config.py של Social Publisher: שם `os.environ[...]` ובדיקת Cloudinary
זרקו חריגה בזמן import, ולכן משתנה חסר הפיל את כל השרת. כאן שום דבר לא זורק
בזמן import: כל משתנה נבדק (תחביר + טווח), ובעיה נרשמת כ"תקלת הגדרה" שמורידה
רק את היכולת שתלויה בה. אם אין סיסמה — השירות חוסם הכול (נכשל-סגור). אם חסר
פרט של גוגל — הכניסה עובדת, וחיבור הדרייב מציג מה חסר.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# ─── Google ──────────────────────────────────────────────────
# drive.readonly: לקרוא את כל התיקיות של החשבון. drive.file: לכתוב רק לקבצים
# שהכלי עצמו יצר. השילוב הזה הוא מה שמונע מהכלי לשנות או למחוק תמונות.
# מקור: https://developers.google.com/workspace/drive/api/guides/api-specific-auth
DRIVE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
)

# מקור לשלוש הכתובות: https://developers.google.com/identity/protocols/oauth2/web-server
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

OAUTH_CALLBACK_PATH = "/oauth/callback"

# ─── גבולות קלט ──────────────────────────────────────────────
PANEL_SECRET_MIN_LEN = 8
PANEL_SECRET_MAX_LEN = 256
TOKEN_KEY_MIN_LEN = 32  # Render generateValue מייצר 44 תווים (base64 של 256 ביט)
DRIVE_ID_MAX_LEN = 200  # מזהי דרייב הם בסביבות 33 תווים; 200 משאיר שוליים רחבים

_DRIVE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9_.-]+\.apps\.googleusercontent\.com")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def is_valid_drive_id(value: object) -> bool:
    """מזהה דרייב תקין: מחרוזת, באורך סביר, רק `[A-Za-z0-9_-]`.

    הבדיקה הזו מחליפה את `_is_folder_within_root` של הפאנל המקורי. כל מזהה
    עובר כאן לפני שהוא נכנס לשאילתה של דרייב, ולכן גרש או רווח לא יכולים
    לשבור את השאילתה.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= DRIVE_ID_MAX_LEN
        and _DRIVE_ID_RE.fullmatch(value) is not None
    )


def _read(env: Mapping[str, str], key: str) -> str | None:
    """קורא משתנה סביבה. ריק או רווחים בלבד נחשב חסר.

    לא `if value := env.get(...)`: כאן ההחלטה "ריק = חסר" מפורשת, ולא תוצאה
    לוואי של truthiness.
    """
    raw = env.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value if value != "" else None


def _parse_base_url(value: str, *, production: bool) -> str | None:
    """מחזיר `scheme://host[:port]` בלי `/` בסוף, או None אם הכתובת לא תקינה."""
    try:
        parts = urlsplit(value)
        port = parts.port  # זורק ValueError על פורט לא מספרי
    except ValueError:
        return None
    if parts.hostname is None or parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local and not production):
        return None
    netloc = parts.hostname if port is None else f"{parts.hostname}:{port}"
    return f"{parts.scheme}://{netloc}"


@dataclass(frozen=True)
class Settings:
    production: bool
    panel_secret: str | None
    panel_secret_problem: str | None
    token_key: str | None
    google_client_id: str | None
    google_client_secret: str | None
    allowed_google_email: str | None
    public_base_url: str | None
    data_dir: Path
    # תיאורים בעברית של מה שחסר כדי לחבר את גוגל. ריק = אפשר לחבר.
    google_problems: tuple[str, ...]
    # כתובת ליצירת קשר בדף הפרטיות הציבורי. ממשתנה סביבה, כדי שבריפו לא יהיו פרטי הלקוח.
    privacy_contact_email: str | None = None

    @property
    def google_configured(self) -> bool:
        return not self.google_problems


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """קורא את ההגדרות. לא זורק: כל בעיה נרשמת ומדווחת דרך השדות."""
    env = os.environ if env is None else env

    app_env = _read(env, "APP_ENV")
    if app_env not in (None, "production", "development"):
        logger.warning("APP_ENV has an unknown value; running as production")
    # ברירת המחדל היא production: עוגייה Secure ובדיקות מחמירות. מצב פיתוח רק בבקשה מפורשת.
    production = app_env != "development"

    panel_secret = _read(env, "WEB_PANEL_SECRET")
    panel_secret_problem = None
    if panel_secret is None:
        panel_secret_problem = "לא הוגדרה סיסמת כניסה (WEB_PANEL_SECRET)."
    elif not PANEL_SECRET_MIN_LEN <= len(panel_secret) <= PANEL_SECRET_MAX_LEN:
        panel_secret_problem = (
            f"סיסמת הכניסה (WEB_PANEL_SECRET) צריכה להיות באורך "
            f"{PANEL_SECRET_MIN_LEN} עד {PANEL_SECRET_MAX_LEN} תווים."
        )
    if panel_secret_problem:
        logger.error("Panel password is missing or invalid; every page is blocked until it is set")
        panel_secret = None

    problems: list[str] = []

    token_key = _read(env, "TOKEN_ENCRYPTION_KEY")
    if token_key is None or len(token_key) < TOKEN_KEY_MIN_LEN:
        problems.append(
            f"חסר מפתח הצפנה (TOKEN_ENCRYPTION_KEY), או שהוא קצר מ-{TOKEN_KEY_MIN_LEN} תווים."
        )
        token_key = None

    client_id = _read(env, "GOOGLE_CLIENT_ID")
    if client_id is None:
        problems.append("חסר מזהה הלקוח של גוגל (GOOGLE_CLIENT_ID).")
    elif _CLIENT_ID_RE.fullmatch(client_id) is None:
        problems.append(
            "מזהה הלקוח של גוגל (GOOGLE_CLIENT_ID) לא נראה תקין. "
            "הוא אמור להסתיים ב-.apps.googleusercontent.com."
        )
        client_id = None

    client_secret = _read(env, "GOOGLE_CLIENT_SECRET")
    if client_secret is None:
        problems.append("חסר הסוד של לקוח גוגל (GOOGLE_CLIENT_SECRET).")
    elif len(client_secret) > 200 or re.search(r"\s", client_secret):
        problems.append("הסוד של לקוח גוגל (GOOGLE_CLIENT_SECRET) לא נראה תקין.")
        client_secret = None

    allowed_email = _read(env, "ALLOWED_GOOGLE_EMAIL")
    if allowed_email is None:
        problems.append("חסרה כתובת חשבון הגוגל המורשה (ALLOWED_GOOGLE_EMAIL).")
    elif len(allowed_email) > 254 or _EMAIL_RE.fullmatch(allowed_email) is None:
        problems.append("כתובת חשבון הגוגל המורשה (ALLOWED_GOOGLE_EMAIL) לא נראית תקינה.")
        allowed_email = None

    # PUBLIC_BASE_URL גובר; אחריו RENDER_EXTERNAL_URL, ש-Render מגדיר לבד לשירות web.
    # מקור: https://render.com/docs/environment-variables
    public_base_url = None
    for key in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"):
        raw = _read(env, key)
        if raw is None:
            continue
        public_base_url = _parse_base_url(raw, production=production)
        if public_base_url is None:
            problems.append(f"הכתובת הציבורית ({key}) לא תקינה. היא צריכה להיות https://שם-השרת, בלי נתיב.")
        break
    if public_base_url is None and production and not any("הכתובת הציבורית" in p for p in problems):
        problems.append("חסרה הכתובת הציבורית של השירות (RENDER_EXTERNAL_URL או PUBLIC_BASE_URL).")

    data_dir = Path(_read(env, "DATA_DIR") or "/var/data")

    privacy_contact = _read(env, "PRIVACY_CONTACT_EMAIL")
    if privacy_contact is not None and (len(privacy_contact) > 254 or _EMAIL_RE.fullmatch(privacy_contact) is None):
        logger.warning("PRIVACY_CONTACT_EMAIL does not look like an email address; the privacy page shows no address")
        privacy_contact = None

    if problems:
        logger.warning("Google connection is not configured: %d problem(s)", len(problems))

    return Settings(
        production=production,
        panel_secret=panel_secret,
        panel_secret_problem=panel_secret_problem,
        token_key=token_key,
        google_client_id=client_id,
        google_client_secret=client_secret,
        allowed_google_email=allowed_email,
        public_base_url=public_base_url,
        data_dir=data_dir,
        google_problems=tuple(problems),
        privacy_contact_email=privacy_contact,
    )
