"""
google_api.py — לקוח הדרייב, דרך ההרשאה של החשבון שחובר ב-OAuth.

ההבדל מ-google_api.py של Social Publisher: שם הלקוח נבנה מ-Service Account
(`_get_credentials`). כאן הוא נבנה מה-refresh token ששמור מוצפן בדיסק.

הלקוח נשמר ב-threading.local (httplib2 אינו thread-safe), ונבנה מחדש כשקובץ
ההרשאה מתחלף (חיבור מחדש) — לפי גרסת הקובץ. הבנייה נעשית במשתנים מקומיים,
והערך מתפרסם ל-thread-local בהצבה אחת רק כשהוא שלם.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app, g
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from config import DRIVE_SCOPES, GOOGLE_TOKEN_URL, is_valid_drive_id
from token_store import StoredGrant, TokenStore, TokenUnreadable

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
IMAGE_MIMES = ("image/jpeg", "image/png")

_local = threading.local()


class GoogleNotConnected(Exception):
    """אין הרשאה שמורה שאפשר להשתמש בה. `reason`: missing / unreadable / not_configured."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Bundle:
    version: tuple[int, int, int]
    grant: StoredGrant
    credentials: Credentials
    drive: object


def _services():
    return current_app.extensions["images2pdf"]


def get_token_store() -> TokenStore:
    store = _services().token_store
    if store is None:
        raise GoogleNotConnected("not_configured")
    return store


def _bundle() -> _Bundle:
    settings = _services().settings
    store = get_token_store()
    version = store.version()
    if version is None:
        raise GoogleNotConnected("missing")
    cached = getattr(_local, "bundle", None)
    if cached is not None and cached.version == version:
        g.google_grant_version = version
        return cached

    try:
        grant = store.load()
    except TokenUnreadable:
        logger.warning("Stored Google grant could not be read (key changed or file damaged)")
        raise GoogleNotConnected("unreadable") from None
    if grant is None:  # נמחק בין stat לקריאה
        raise GoogleNotConnected("missing")

    credentials = Credentials(
        token=None,
        refresh_token=grant.refresh_token,
        token_uri=GOOGLE_TOKEN_URL,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=list(DRIVE_SCOPES),
    )
    # cache_discovery=False: בלי מטמון discovery שגדל בכל thread (כמו בפאנל המקורי).
    # עם credentials, googleapiclient בונה AuthorizedHttp עם timeout של 60 שניות.
    # מקור: googleapiclient/_auth.py authorized_http, googleapiclient/http.py build_http
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    bundle = _Bundle(version=version, grant=grant, credentials=credentials, drive=drive)
    _local.bundle = bundle
    g.google_grant_version = version
    return bundle


def get_drive_service():
    return _bundle().drive


def get_credentials() -> Credentials:
    return _bundle().credentials


def get_stored_grant() -> StoredGrant:
    return _bundle().grant


def forget_cached_clients() -> None:
    _local.bundle = None


# ─── סיווג כשלי הרשאה ─────────────────────────────────────────

def refresh_error_kind(exc: RefreshError) -> str:
    """invalid_grant / invalid_client / other.

    google-auth זורק `RefreshError(error_details, response_data, retryable=...)`,
    ו-`response_data` הוא ה-JSON של שרת הטוקנים. מקור:
    google/oauth2/_client.py `_handle_error_response`.
    כש-Google דורש אימות מחדש (`invalid_grant` עם error_subtype של rapt),
    google-auth זורק הודעה בלי ה-JSON. מקור: google/oauth2/reauth.py `refresh_grant`.
    """
    args = exc.args
    if len(args) >= 2 and isinstance(args[1], dict):
        error = args[1].get("error")
        if error == "invalid_grant":
            return "invalid_grant"
        if error in ("invalid_client", "unauthorized_client"):
            return "invalid_client"
        return "other"
    if args and isinstance(args[0], str) and args[0].startswith("Reauthentication is needed"):
        return "invalid_grant"
    return "other"


def http_error_reason(exc: HttpError) -> str:
    """ה-reason מגוף השגיאה של דרייב (למשל insufficientFilePermissions), או ''.

    מבנה הגוף: {"error": {"errors": [{"reason": ...}], ...}}. מקור:
    https://developers.google.com/workspace/drive/api/guides/handle-errors
    """
    try:
        data = json.loads(exc.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    for key in ("errors", "details"):
        items = error.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("reason"), str):
                    return item["reason"]
    return ""


def http_status(exc: HttpError) -> int | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


# ─── שאילתות ──────────────────────────────────────────────────

def escape_query_value(text: str) -> str:
    """Escape לערך בתוך מרכאות בודדות בשאילתת דרייב: `\\` → `\\\\`, `'` → `\\'`.

    הסדר חשוב: קודם הלוכסן ואחר כך הגרש, אחרת הלוכסן שנוסף לגרש יוכפל.
    מקור: https://developers.google.com/workspace/drive/api/guides/search-files
    ("name contains 'quinn\\'s paper\\\\essay'")
    """
    if not isinstance(text, str):
        raise TypeError("query value must be a string")
    return text.replace("\\", "\\\\").replace("'", "\\'")


def drive_search_folders(text: str, page_token: str | None = None, page_size: int = 100) -> dict:
    """חיפוש תיקיות לפי שם.

    `name contains` בדרייב הוא התאמת קידומת ולא תת-מחרוזת: 'Hello' מוצא את
    HelloWorld, ו-'World' לא. מקור:
    https://developers.google.com/workspace/drive/api/guides/ref-search-terms
    """
    query = (
        f"mimeType = '{FOLDER_MIME}' and name contains '{escape_query_value(text)}' "
        "and trashed = false"
    )
    result = (
        get_drive_service()
        .files()
        .list(
            q=query,
            fields="nextPageToken, incompleteSearch, files(id, name, parents)",
            pageSize=page_size,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return _list_result(result)


def drive_list_folder(
    folder_id: str,
    page_token: str | None = None,
    *,
    folders_only: bool = False,
    page_size: int = 100,
) -> dict:
    """תיקיות ותמונות JPEG/PNG בתוך תיקייה (בלי סרטונים ובלי קבצים אחרים).

    `folder_id` חייב לעבור `is_valid_drive_id` לפני הקריאה.
    """
    if not is_valid_drive_id(folder_id):
        raise ValueError("invalid folder id")
    if folders_only:
        type_filter = f"mimeType = '{FOLDER_MIME}'"
    else:
        mimes = (FOLDER_MIME, *IMAGE_MIMES)
        type_filter = "(" + " or ".join(f"mimeType = '{m}'" for m in mimes) + ")"
    query = f"'{folder_id}' in parents and {type_filter} and trashed = false"
    result = (
        get_drive_service()
        .files()
        .list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, thumbnailLink, parents, capabilities(canEdit))",
            pageSize=page_size,
            pageToken=page_token,
            orderBy="folder,name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return _list_result(result)


def _list_result(result: object) -> dict:
    if not isinstance(result, dict):
        return {"files": [], "nextPageToken": None, "incompleteSearch": False}
    files = result.get("files")
    token = result.get("nextPageToken")
    return {
        "files": [f for f in files if isinstance(f, dict)] if isinstance(files, list) else [],
        "nextPageToken": token if isinstance(token, str) else None,
        "incompleteSearch": result.get("incompleteSearch") is True,
    }


def drive_get(file_id: str, fields: str) -> dict:
    if not is_valid_drive_id(file_id):
        raise ValueError("invalid file id")
    result = get_drive_service().files().get(fileId=file_id, fields=fields, supportsAllDrives=True).execute()
    return result if isinstance(result, dict) else {}


def drive_create_folder(name: str, parent_id: str | None = None) -> dict:
    body: dict = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id is not None:
        body["parents"] = [parent_id]
    return get_drive_service().files().create(body=body, fields="id, name, webViewLink").execute()


def drive_upload_bytes(name: str, data: bytes, mimetype: str, parent_id: str) -> dict:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype, resumable=False)
    return (
        get_drive_service()
        .files()
        .create(
            body={"name": name, "parents": [parent_id]},
            media_body=media,
            fields="id, name, size, parents, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


# ─── הורדה ────────────────────────────────────────────────────

# googleapiclient מוריד כברירת מחדל בחתיכות של 100MB, וכל חתיכה נקראת כולה לזיכרון
# לפני הכתיבה לקובץ. 4MB מגבילים את מה שיושב בזיכרון בכל רגע.
# מקור: googleapiclient/http.py, DEFAULT_CHUNK_SIZE = 100 * 1024 * 1024 ו-MediaIoBaseDownload.next_chunk
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024


class DownloadTooLarge(Exception):
    """הקובץ גדול מ-MAX_DOWNLOAD_BYTES."""


def drive_download_to_file(file_id: str, dest: "os.PathLike[str] | str") -> int:
    """מוריד קובץ מדרייב לקובץ בדיסק (ולא לזיכרון). מחזיר את מספר הבתים.

    מחליף את `drive_download_bytes` של הפאנל המקורי, שטען את כל הקובץ ל-BytesIO.
    זורק DownloadTooLarge, HttpError או OSError; בכל כשל הקובץ החלקי נמחק.
    """
    if not is_valid_drive_id(file_id):
        raise ValueError("invalid file id")
    meta = drive_get(file_id, "size")
    size = meta.get("size")
    if isinstance(size, str) and size.isdigit() and int(size) > MAX_DOWNLOAD_BYTES:
        raise DownloadTooLarge(int(size))
    request = get_drive_service().files().get_media(fileId=file_id, supportsAllDrives=True)
    dest_path = Path(dest)
    try:
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=DOWNLOAD_CHUNK_BYTES)
            done = False
            while not done:
                _, done = downloader.next_chunk()
                if fh.tell() > MAX_DOWNLOAD_BYTES:
                    raise DownloadTooLarge(fh.tell())
            written = fh.tell()
    except BaseException:
        dest_path.unlink(missing_ok=True)
        raise
    return written


# ─── תמונות ממוזערות ──────────────────────────────────────────

def is_allowed_thumbnail_url(url: object) -> bool:
    """האם מותר לשלוח לכתובת הזו את טוקן הגישה שלנו.

    `thumbnailLink` מגיע מגוגל, אבל לפני שמצרפים אליו `Authorization: Bearer`
    בודקים אותו לפי מבנה: https, פורט ברירת מחדל, ומארח שהוא googleusercontent.com
    או תת-דומיין שלו. בדיקת `endswith("googleusercontent.com")` בלבד הייתה
    מקבלת גם `evilgoogleusercontent.com`.
    """
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
        return False
    return host == "googleusercontent.com" or host.endswith(".googleusercontent.com")


def thumbnail_url_host(url: object) -> str:
    """רק שם המארח, ללוג. הכתובת המלאה היא קישור גישה, ולא נכנסת ללוג."""
    if not isinstance(url, str):
        return "<not a string>"
    try:
        return urlsplit(url).hostname or "<no host>"
    except ValueError:
        return "<unparseable>"
