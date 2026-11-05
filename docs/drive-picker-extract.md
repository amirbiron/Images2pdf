# חילוץ קוד: בורר Google Drive ומסך הכניסה

**מקור:** הריפו `amirbiron/multi-channel-publisher`, קומיט `2432d2f`, נכון ל-5.11.2026.

**מה יש כאן:** כל בלוק קוד הוא העתקה מדויקת, תו בתו, של טווח שורות רציף מהקובץ המקורי, בלי שיפורים ובלי השמטות בתוך הטווח. מעל כל בלוק כתובים נתיב הקובץ, שם הפונקציה או הקטע, ומספרי השורות. כשמקובץ הועתק רק חלק, השורות שבין הטווחים שייכות לחלקים אחרים של הפאנל (גיליון הפוסטים, פרסום לרשתות, לוח שנה וכו') ולא לבורר או לכניסה.

**איך הקטעים מחוברים:** הדפדפן נכנס דרך מסך הכניסה (`_check_auth` ← `_login_page` ← עוגייה ב-`_set_auth_cookie`). אחרי הכניסה הפרונט טוען את `/api/config` כדי לקבל את `driveFolderId`, פותח את המודאל `drive-modal`, ומבקש רשימת קבצים מ-`/api/drive/files`. השרת בודק עם `_is_folder_within_root` שהתיקייה בתוך תיקיית השורש, וקורא ל-`drive_list_folder`, שמשתמש ב-`get_drive_service` עם הרשאות חשבון שירות (Service Account).

## לפני שמתחילים: עובדות על הקוד הזה שכדאי לדעת

1. **הגישה לדרייב היא דרך Service Account, לא דרך OAuth.** `_get_credentials` ב-`google_api.py` בונה הרשאה מה-JSON שבמשתנה `GOOGLE_SERVICE_ACCOUNT_JSON`, עם `GOOGLE_SCOPES`.
2. **קבצי ה-OAuth שייכים ל-Google Business Profile, לא לדרייב.** `scripts/google_oauth.py` מבקש רק את ה-scope `https://www.googleapis.com/auth/business.manage`, ו-`channels/google_auth.py` מנהל טוקן גישה ל-GBP מתוך `GBP_OAUTH_CLIENT_ID`, `GBP_OAUTH_CLIENT_SECRET` ו-`GBP_REFRESH_TOKEN`. אף קטע דרייב בקובץ הזה לא משתמש בהם. הם מצורפים כי התבקשו.
3. **ייבוא `config.py` דורש משתנים שלא קשורים לדרייב.** בזמן import, `GOOGLE_SERVICE_ACCOUNT_JSON` ו-`SPREADSHEET_ID` נקראים עם `os.environ[...]` (משתנה חסר ← `KeyError`), ובדיקת Cloudinary זורקת `RuntimeError` כשאין פרטי Cloudinary. `google_api.py` מייבא מ-`config`, ולכן כל אלה נדרשים גם כשמשתמשים רק בדרייב. בנוסף, `config.py` עושה `from config_constants import *`, ולכן `config_constants.py` מצורף במלואו.
4. **`GOOGLE_SCOPES` כולל גם הרשאה לגיליונות** (`spreadsheets`), לצד `drive.readonly`.
5. **התמונות הממוזערות בבורר לא עוברות דרך השרת.** ב-`loadDriveFolder` התמונה נטענת ישירות מ-`f.thumbnailLink` שגוגל מחזיר. נקודת הקצה `/api/drive/thumbnail/<file_id>` (פרוקסי שמושך את התמונה עם הטוקן של חשבון השירות) משמשת בפאנל בטבלת הפוסטים ובתצוגה המוגדלת, לא בבורר עצמו.
6. **ל-`api_drive_thumbnail` יש מסלול גיבוי שקורא את Google Sheet.** כשגוגל מחזיר קובץ בלי `parents`, ההרשאה נבדקת דרך `_is_known_drive_file`, שמחפש את מזהה הקובץ בעמודה `drive_file_id` בגיליון. לכן צורפו גם `sheets_read_all_rows`, `get_sheets_service` והעזרים שלהם. בכלי בלי גיליון, המסלול הזה יצטרך החלטה.
7. **`drive_list_folder` מחזיר את כל סוגי הקבצים**, 50 בכל עמוד, תיקיות קודם ואחר כך לפי `modifiedTime` מהחדש לישן. אין סינון לתמונות בלבד. השרת מחזיר `nextPageToken` ו-`/api/drive/files` מקבל `page_token`, אבל `loadDriveFolder` בפרונט לא מבקש עמוד נוסף, כך שמוצג רק העמוד הראשון שגוגל מחזיר (עד 50 פריטים).
8. **בחירה מרובה תלויה בטופס הפוסט.** `_isMultiSelectAllowed` קורא ל-`getSelectedChannels`, שקורא את תיבות הסימון של הערוצים (`form-ch-ig` וכו'). בחירה מרובה מותרת רק כשנבחר IG לבד, ועד 10 קבצים. `confirmDriveSelection` כותב לשדות של טופס הפוסט (`form-drive-file-id`, `selected-file-name`, `drive-file-display` וכו'). לכן צורפו גם השדות האלה מה-HTML.
9. **מסך הכניסה:** הסיסמה נשלחת בכתובת כ-`?token=`. `_set_auth_cookie` שומר עוגייה `panel_token` שמכילה HMAC של הסוד (לא את הסוד עצמו), עם HttpOnly, ‏SameSite=Lax, תוקף 30 יום, ו-Secure רק כשהבקשה הגיעה ב-HTTPS (כולל `X-Forwarded-Proto`). בלי `WEB_PANEL_SECRET` אין אימות בכלל. לעמוד הכניסה יש CSS משלו בתוך ה-HTML, לא מ-`style.css`.
10. **`/api/config` דורש כניסה.** מעליו כתוב בהערה "public, non-sensitive", אבל `_check_auth` פוטר רק את `/static/`, ‏`/api/health` ו-`/privacy`.
11. **`web_app.py` — בלוק הייבוא הועתק כמו שהוא**, ולכן הוא כולל גם ייבוא של מודולים שלא קשורים לבורר (`media_processor`, `validator`, `notifications` ועוד). מה שהקטעים כאן צריכים בפועל: `hashlib`, `hmac`, `logging`, `os`, `re`, `sys`, `requests as http_requests`, ‏`Flask, Response, jsonify, render_template, request`, ‏`COL_DRIVE_FILE_ID`, ‏`sheets_read_all_rows`, ‏`drive_list_folder`, ‏`get_drive_service`, ‏`release_memory`, וב-`api_config` גם `SHEET_COLUMNS`, `STATUS_*`, `VALID_NETWORKS`, `POST_TYPE_*` (מ-`config_constants.py`).
12. **ייבוא `channels.google_auth` בפרויקט המקורי טוען את כל החבילה `channels`**, כי `channels/__init__.py` מייבא את כל הערוצים (Instagram, Facebook, GBP, LinkedIn). הקובץ `google_auth.py` עצמו תלוי רק ב-`requests` וב-`config` (ייבוא עצל בתוך `get_oauth_manager`).
13. **CSS:** `@keyframes spin` מוגדר ב-`style.css` פעמיים, בשורות 639–642 ובשורות 1567–1569. הועתקה ההגדרה שבקטע Animations (השנייה). התאמות המובייל של המודאל והבורר נמצאות בתוך `@media (max-width: 640px)` אחד גדול, ולכן הן מופיעות כקטעים נפרדים, עם שורת הפתיחה ושורת הסגירה של ה-media query בבלוקים משלהן.

## 1. `config.py` — פרטי הגישה לגוגל ו-`GOOGLE_SCOPES`

**`config.py`** · תיעוד המודול, ייבוא, ומשתני Google · שורות 1–17

```python
"""
config.py — הגדרות סביבה וקבועים

Pure constants (column names, status values, etc.) live in config_constants.py
and are re-exported here so existing imports keep working.
"""

import os
import json

# Re-export all pure constants
from config_constants import *  # noqa: F401,F403

# ─── Google ──────────────────────────────────────────────────
GOOGLE_SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]  # כל ה-JSON כמחרוזת
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
```

**`config.py`** · בדיקת Cloudinary (רצה בזמן import) · שורות 30–45

> לא קשור לדרייב, אבל זורק `RuntimeError` בזמן ייבוא `config` ולכן חוסם גם את `google_api.py`.

```python
# ─── Cloudinary ──────────────────────────────────────────────
# אפשרות 1 (מועדפת): CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
#   ה-SDK קורא את CLOUDINARY_URL אוטומטית
# אפשרות 2: שלושה משתנים נפרדים
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")

# וידוא שיש לפחות אחת מהאפשרויות
if not os.environ.get("CLOUDINARY_URL") and not all(
    [CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]
):
    raise RuntimeError(
        "Cloudinary credentials missing. Set CLOUDINARY_URL or "
        "CLOUDINARY_CLOUD_NAME + CLOUDINARY_API_KEY + CLOUDINARY_API_SECRET"
    )
```

**`config.py`** · `GOOGLE_SCOPES` · שורות 47–51

```python
# ─── Google Scopes ───────────────────────────────────────────
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
```

**`config.py`** · משתני Google Business Profile — OAuth 2.0 · שורות 53–58

> תלות של `get_oauth_manager` ב-`channels/google_auth.py`.

```python
# ─── Google Business Profile — OAuth 2.0 ─────────────────
GBP_ACCOUNT_ID = os.environ.get("GBP_ACCOUNT_ID", "")            # e.g. "accounts/123456789"
GBP_DEFAULT_LOCATION_ID = os.environ.get("GBP_DEFAULT_LOCATION_ID", "")  # e.g. "locations/123456789"
GBP_OAUTH_CLIENT_ID = os.environ.get("GBP_OAUTH_CLIENT_ID", "")
GBP_OAUTH_CLIENT_SECRET = os.environ.get("GBP_OAUTH_CLIENT_SECRET", "")
GBP_REFRESH_TOKEN = os.environ.get("GBP_REFRESH_TOKEN", "")
```

**`config.py`** · `get_google_sa_info` · שורות 87–89

```python
def get_google_sa_info() -> dict:
    """פרסור ה-Service Account JSON מתוך env var."""
    return json.loads(GOOGLE_SA_JSON)
```

## 2. `config_constants.py` — הקובץ המלא

מצורף במלואו כי `config.py` עושה `from config_constants import *`, ו-`web_app.py` מייבא ממנו את `COL_DRIVE_FILE_ID` (בשימוש ב-`_is_known_drive_file`) ואת הקבועים שמוחזרים ב-`api_config`.

**`config_constants.py`** · הקובץ כולו · שורות 1–143

```python
"""
config_constants.py — Pure constants that do NOT require any credentials.

Import from here when you only need column names, status values, etc.
The full config.py re-exports everything from this module, so existing
code that does `from config import COL_ID` continues to work.
"""

import re
from zoneinfo import ZoneInfo

# ─── Timezone ────────────────────────────────────────────────
TZ_IL = ZoneInfo("Asia/Jerusalem")

# ─── Sheet Column Names (existing) ──────────────────────────
COL_ID = "id"
COL_STATUS = "status"
COL_NETWORK = "network"
COL_POST_TYPE = "post_type"
COL_PUBLISH_AT = "publish_at"
COL_CAPTION_IG = "caption_ig"
COL_CAPTION_FB = "caption_fb"
COL_DRIVE_FILE_ID = "drive_file_id"
COL_COVER_FILE_ID = "cover_drive_file_id"
COL_CLOUDINARY_URL = "cloudinary_url"
COL_RESULT = "result"
COL_ERROR = "error"

# ─── Sheet Column Names (new — multi-channel) ───────────────
COL_CAPTION = "caption"
COL_CAPTION_GBP = "caption_gbp"
COL_CAPTION_LI = "caption_li"
COL_LI_AUTHOR_URN = "li_author_urn"
COL_GBP_POST_TYPE = "gbp_post_type"
COL_CTA_TYPE = "cta_type"
COL_CTA_URL = "cta_url"
COL_GOOGLE_LOCATION_ID = "google_location_id"
COL_HASHTAGS = "hashtags"
COL_FIRST_COMMENT = "first_comment"
COL_SOURCE = "source"
COL_RETRY_COUNT = "retry_count"
COL_LOCKED_AT = "locked_at"
COL_PROCESSING_BY = "processing_by"
COL_PUBLISHED_CHANNELS = "published_channels"
COL_FAILED_CHANNELS = "failed_channels"

# ─── Status Values ───────────────────────────────────────────
STATUS_DRAFT = "DRAFT"
STATUS_READY = "READY"
STATUS_PROCESSING = "PROCESSING"
STATUS_POSTED = "POSTED"
STATUS_PARTIAL = "PARTIAL"
STATUS_ERROR = "ERROR"

# ─── Network Values ─────────────────────────────────────────
NETWORK_IG = "IG"
NETWORK_FB = "FB"
NETWORK_GBP = "GBP"
NETWORK_BOTH = "IG+FB"
NETWORK_IG_GBP = "IG+GBP"
NETWORK_FB_GBP = "FB+GBP"
NETWORK_LI = "LI"
NETWORK_IG_LI = "IG+LI"
NETWORK_FB_LI = "FB+LI"
NETWORK_GBP_LI = "GBP+LI"
NETWORK_ALL_THREE = "IG+FB+GBP"
NETWORK_IG_FB_LI = "IG+FB+LI"
NETWORK_IG_GBP_LI = "IG+GBP+LI"
NETWORK_FB_GBP_LI = "FB+GBP+LI"
NETWORK_IG_FB_GBP_LI = "IG+FB+GBP+LI"
NETWORK_ALL = "ALL"

# Set of all valid network values for validation
VALID_NETWORKS = {
    NETWORK_IG, NETWORK_FB, NETWORK_GBP, NETWORK_LI,
    NETWORK_BOTH, NETWORK_IG_GBP, NETWORK_FB_GBP,
    NETWORK_IG_LI, NETWORK_FB_LI, NETWORK_GBP_LI,
    NETWORK_ALL_THREE, NETWORK_IG_FB_LI, NETWORK_IG_GBP_LI,
    NETWORK_FB_GBP_LI, NETWORK_IG_FB_GBP_LI,
    NETWORK_ALL,
}

# ─── Post Type Values ──────────────────────────────────────
POST_TYPE_FEED = "FEED"
POST_TYPE_REELS = "REELS"
POST_TYPE_TEXT = "TEXT"

# ─── GBP Post Type Values ──────────────────────────────────
GBP_POST_TYPE_STANDARD = "STANDARD"
GBP_POST_TYPE_EVENT = "EVENT"
GBP_POST_TYPE_OFFER = "OFFER"

# ─── LinkedIn Limits ───────────────────────────────────────
LI_CAPTION_MAX_LENGTH = 3000
LI_URN_PATTERN = re.compile(r"^urn:li:(person|organization):[A-Za-z0-9_-]+$")

# ─── Source Values ──────────────────────────────────────────
SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"
SOURCE_AI_PANEL = "ai-panel"

# ─── CTA Types (Google Business Profile) ────────────────────
# ─── Lock Timeout ─────────────────────────────────────────────
LOCK_TIMEOUT_MINUTES = 10

CTA_LEARN_MORE = "LEARN_MORE"
CTA_CALL = "CALL"
CTA_BOOK = "BOOK"

# ─── Sheet Schema ───────────────────────────────────────────
# Canonical column order for the Google Sheet.
# Existing IG/FB rows continue to work — new columns are optional
# and default to empty string when absent.
SHEET_COLUMNS = [
    COL_ID,
    COL_STATUS,
    COL_NETWORK,
    COL_POST_TYPE,
    COL_PUBLISH_AT,
    COL_CAPTION,
    COL_CAPTION_IG,
    COL_CAPTION_FB,
    COL_CAPTION_GBP,
    COL_CAPTION_LI,
    COL_LI_AUTHOR_URN,
    COL_GBP_POST_TYPE,
    COL_CTA_TYPE,
    COL_CTA_URL,
    COL_GOOGLE_LOCATION_ID,
    COL_HASHTAGS,
    COL_FIRST_COMMENT,
    COL_DRIVE_FILE_ID,
    COL_COVER_FILE_ID,
    COL_CLOUDINARY_URL,
    COL_SOURCE,
    COL_RESULT,
    COL_ERROR,
    COL_RETRY_COUNT,
    COL_LOCKED_AT,
    COL_PROCESSING_BY,
    COL_PUBLISHED_CHANNELS,
    COL_FAILED_CHANNELS,
]
```

## 3. `google_api.py` — לקוח הדרייב

**`google_api.py`** · תיעוד המודול, ייבוא ו-`logger` · שורות 1–24

```python
"""
google_api.py — פונקציות עזר ל-Google Sheets ו-Google Drive
"""

import io
import logging
import socket
import threading
import time
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from config import (
    GOOGLE_SCOPES,
    SPREADSHEET_ID,
    SHEET_NAME,
    get_google_sa_info,
)

logger = logging.getLogger(__name__)
```

**`google_api.py`** · `_local`, ‏`_get_credentials`, ‏`get_sheets_service`, ‏`get_drive_service` · שורות 27–58

> `get_sheets_service` נמצא בין הפונקציות שהתבקשו, והוא תלות של `sheets_read_all_rows` (סעיף 3א).

```python
# ═══════════════════════════════════════════════════════════════
#  Google Clients — thread-local (httplib2 is NOT thread-safe)
# ═══════════════════════════════════════════════════════════════

_local = threading.local()


def _get_credentials():
    sa_info = get_google_sa_info()
    return service_account.Credentials.from_service_account_info(
        sa_info, scopes=GOOGLE_SCOPES
    )


def get_sheets_service():
    svc = getattr(_local, "sheets_service", None)
    if svc is None:
        creds = _get_credentials()
        # cache_discovery=False avoids the in-process discovery doc cache that
        # silently grows per-thread on Render's 512MB worker.
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        _local.sheets_service = svc
    return svc


def get_drive_service():
    svc = getattr(_local, "drive_service", None)
    if svc is None:
        creds = _get_credentials()
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        _local.drive_service = svc
    return svc
```

**`google_api.py`** · `drive_get_media_metadata`, ‏`drive_download_bytes` · שורות 274–310

```python
def drive_get_media_metadata(file_id: str) -> dict:
    """
    מביא metadata מורחב של קובץ מדיה מ-Drive — כולל מימדי תמונה/וידאו.

    imageMediaMetadata: width, height, rotation
    videoMediaMetadata: width, height, durationMillis
    """
    svc = get_drive_service()
    return (
        svc.files()
        .get(
            fileId=file_id,
            fields="id,name,mimeType,size,imageMediaMetadata,videoMediaMetadata",
            supportsAllDrives=True,
        )
        .execute()
    )


def drive_download_bytes(file_id: str) -> bytes:
    """
    מוריד את תוכן הקובץ כ-bytes מ-Drive (alt=media).
    """
    svc = get_drive_service()
    request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            logger.debug(f"Drive download {file_id}: {int(status.progress() * 100)}%")

    logger.info(f"Downloaded {file_id} ({buffer.tell()} bytes)")
    return buffer.getvalue()
```

**`google_api.py`** · `drive_list_folder` · שורות 322–352

```python
def drive_list_folder(folder_id: str, page_token: Optional[str] = None) -> dict:
    """
    מחזיר רשימת קבצים ותיקיות בתוך תיקייה ב-Drive.
    תומך ב-pagination.
    הקבצים ממוינים לפי תאריך עדכון יורד (החדשים ביותר ראשונים),
    כאשר תיקיות מופיעות לפני קבצים רגילים.
    שימוש ב-modifiedTime ולא ב-createdTime כי הוא מותאם לאוספים גדולים
    (ראו התיעוד של Google Drive API על orderBy).
    """
    svc = get_drive_service()
    query = f"'{folder_id}' in parents and trashed = false"
    fields = "nextPageToken, files(id, name, mimeType, size, thumbnailLink, createdTime, modifiedTime)"

    result = (
        svc.files()
        .list(
            q=query,
            fields=fields,
            pageSize=50,
            pageToken=page_token,
            orderBy="folder,modifiedTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )

    return {
        "files": result.get("files", []),
        "nextPageToken": result.get("nextPageToken"),
    }
```

### 3א. תלויות: קריאת הגיליון (בשימוש ב-`_is_known_drive_file`)

**`google_api.py`** · `_SHEETS_RETRYABLE_EXCEPTIONS`, ‏`_SHEETS_READ_MAX_ATTEMPTS`, ‏`_SHEETS_READ_RETRY_DELAY`, ‏`_is_retryable_http_error`, ‏`sheets_read_all_rows` · שורות 65–138

```python
# שגיאות רשת/timeout שמצדיקות retry בקריאה ל-Google Sheets.
# ב-Python 3.10+ (הפרויקט רץ על 3.11) `socket.timeout` הוא alias ל-TimeoutError,
# לכן מספיק לציין את TimeoutError. ConnectionError מכסה את משפחת
# ConnectionRefusedError/ConnectionResetError/BrokenPipeError.
# socket.gaierror מצוין במפורש כי הוא OSError ישיר (כשל DNS) ואינו מכוסה
# על-ידי ConnectionError. לא נכלל OSError "חשוף" כדי שלא נתפוס שגיאות
# לא-רשתיות כמו FileNotFoundError/PermissionError שלא יעזור להן retry.
# HttpError נטפל בנפרד — ורק עבור סטטוסים זמניים (5xx / 429).
_SHEETS_RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    socket.gaierror,
)

_SHEETS_READ_MAX_ATTEMPTS = 3
_SHEETS_READ_RETRY_DELAY = 2  # שניות


def _is_retryable_http_error(exc: HttpError) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return status is not None and (status == 429 or 500 <= status < 600)


def sheets_read_all_rows() -> tuple[list[str], list[list[str]]]:
    """
    קורא את כל הטבלה.
    מחזיר (header, rows) — כאשר rows הוא רשימה של רשימות (שורות).

    כולל retry logic: עד 3 ניסיונות עם המתנה של 2 שניות בין ניסיונות,
    במקרה של שגיאת רשת/timeout בחיבור ל-Google Sheets API.
    """
    rng = f"{SHEET_NAME}!A:AZ"

    for attempt in range(1, _SHEETS_READ_MAX_ATTEMPTS + 1):
        try:
            svc = get_sheets_service()
            resp = (
                svc.spreadsheets()
                .values()
                .get(spreadsheetId=SPREADSHEET_ID, range=rng)
                .execute()
            )
            break
        except _SHEETS_RETRYABLE_EXCEPTIONS as exc:
            logger.warning(
                "Google Sheets connection failed, attempt %d/%d (%s: %s)",
                attempt, _SHEETS_READ_MAX_ATTEMPTS, type(exc).__name__, exc,
            )
            if attempt == _SHEETS_READ_MAX_ATTEMPTS:
                raise
            time.sleep(_SHEETS_READ_RETRY_DELAY)
        except HttpError as exc:
            if not _is_retryable_http_error(exc):
                raise
            logger.warning(
                "Google Sheets connection failed, attempt %d/%d (HTTP %s)",
                attempt, _SHEETS_READ_MAX_ATTEMPTS,
                getattr(getattr(exc, "resp", None), "status", "?"),
            )
            if attempt == _SHEETS_READ_MAX_ATTEMPTS:
                raise
            time.sleep(_SHEETS_READ_RETRY_DELAY)

    values = resp.get("values", [])
    if not values:
        return [], []

    header = values[0]
    rows = values[1:]
    return header, rows
```

## 4. `scripts/google_oauth.py` — הפקת Refresh Token (הקובץ המלא)

סקריפט חד-פעמי שמריצים מקומית. הוא מנסה לטעון `.env` עם `python-dotenv` אם הספרייה מותקנת; היא לא נמצאת ב-`requirements.txt`.

**`scripts/google_oauth.py`** · הקובץ כולו · שורות 1–160

```python
#!/usr/bin/env python3
"""
google_oauth.py — One-time OAuth flow to obtain a Google Refresh Token for GBP.

Usage:
    1. Set GBP_OAUTH_CLIENT_ID and GBP_OAUTH_CLIENT_SECRET in your .env
    2. Run: python scripts/google_oauth.py
    3. Open the printed URL in your browser and authorize
    4. Paste the redirect URL (or just the code) back here
    5. Copy the refresh token to your .env as GBP_REFRESH_TOKEN
"""

import os
import sys
import urllib.parse

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CLIENT_ID = os.environ.get("GBP_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GBP_OAUTH_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "https://www.googleapis.com/auth/business.manage"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GBP_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"


def build_auth_url(client_id: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=30)

    if resp.status_code != 200:
        print(f"Error: Token exchange failed ({resp.status_code})")
        print(resp.text)
        sys.exit(1)

    return resp.json()


def fetch_gbp_accounts(access_token: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(GBP_ACCOUNTS_URL, headers=headers, timeout=15)

    if resp.status_code == 200:
        data = resp.json()
        accounts = data.get("accounts", [])
        if accounts:
            print("  GBP Accounts found:")
            for acc in accounts:
                name = acc.get("name", "")
                account_name = acc.get("accountName", "")
                print(f"    GBP_ACCOUNT_ID={name}")
                print(f"    Name: {account_name}")
        else:
            print("  No GBP accounts found.")
    else:
        print(f"  Could not fetch accounts ({resp.status_code}): {resp.text[:200]}")


def main():
    client_id = CLIENT_ID
    client_secret = CLIENT_SECRET

    if not client_id or not client_secret:
        print("Error: GBP_OAUTH_CLIENT_ID and GBP_OAUTH_CLIENT_SECRET must be set.")
        sys.exit(1)

    auth_url = build_auth_url(client_id)

    print()
    print("=" * 60)
    print("  Google Business Profile — Refresh Token Generator")
    print("=" * 60)
    print()
    print("Step 1: Open this URL in your browser:")
    print()
    print(f"  {auth_url}")
    print()
    print("Step 2: Authorize, then paste the full redirect URL here.")
    print("        (The page will show an error — that's OK!)")
    print("        Copy the URL from the browser address bar.")
    print()

    redirect_url = input("Paste redirect URL: ").strip()

    # Extract code from URL or raw code
    if redirect_url.startswith("http"):
        parsed = urllib.parse.urlparse(redirect_url)
        query = urllib.parse.parse_qs(parsed.query)
        if "code" not in query:
            print("Error: No 'code' parameter found in URL.")
            sys.exit(1)
        code = query["code"][0]
    else:
        code = redirect_url

    print()
    print("Exchanging authorization code for tokens...")

    data = exchange_code(code, client_id, client_secret)

    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    expires_in = data.get("expires_in", 0)

    print()
    print("=" * 60)
    print("  Success!")
    print("=" * 60)
    print()

    if refresh_token:
        print(f"  GBP_REFRESH_TOKEN={refresh_token}")
        print()
        print(f"  Access token expires in: {expires_in // 3600} hours")
        print("  Refresh token does NOT expire (auto-renews).")
    else:
        print("  Warning: No refresh token returned!")
        print("  Make sure 'access_type=offline' and 'prompt=consent' were used.")

    # Fetch GBP accounts
    print()
    print("-" * 60)
    print("  Fetching GBP accounts...")
    print("-" * 60)
    print()
    fetch_gbp_accounts(access_token)
    print()


if __name__ == "__main__":
    main()
```

## 5. `channels/google_auth.py` — רענון טוקן OAuth (הקובץ המלא)

**`channels/google_auth.py`** · הקובץ כולו: `GoogleOAuthManager`, ‏`OAuthRefreshError`, ‏`get_oauth_manager`, ‏`reset_oauth_manager` · שורות 1–153

```python
"""
google_auth.py — Google OAuth 2.0 token management for GBP.

Handles access-token acquisition and automatic refresh using a
long-lived refresh token (stored as env var).  The token is kept
in-memory and refreshed transparently when it expires.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Google's OAuth 2.0 token endpoint
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Refresh the token 5 minutes before it actually expires
_EXPIRY_MARGIN_SECONDS = 300


class GoogleOAuthManager:
    """
    Manages a single OAuth 2.0 access token for the Google Business
    Profile API, refreshing it automatically when needed.

    Usage::

        mgr = GoogleOAuthManager(client_id, client_secret, refresh_token)
        headers = mgr.get_auth_headers()   # {"Authorization": "Bearer …"}
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        if not all([client_id, client_secret, refresh_token]):
            raise ValueError(
                "GBP OAuth credentials incomplete. "
                "Set GBP_OAUTH_CLIENT_ID, GBP_OAUTH_CLIENT_SECRET, and GBP_REFRESH_TOKEN."
            )

        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token

        self._access_token: str | None = None
        self._expires_at: float = 0.0  # epoch seconds
        self._lock = threading.Lock()

    # ── public API ───────────────────────────────────────────

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._is_token_valid():
            return self._access_token  # type: ignore[return-value]

        with self._lock:
            # Double-check after acquiring lock
            if self._is_token_valid():
                return self._access_token  # type: ignore[return-value]
            self._refresh()
            return self._access_token  # type: ignore[return-value]

    def get_auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict ready for requests."""
        return {"Authorization": f"Bearer {self.get_access_token()}"}

    def force_refresh(self) -> str:
        """Force a token refresh regardless of expiry. Returns new token."""
        with self._lock:
            self._refresh()
            return self._access_token  # type: ignore[return-value]

    # ── internals ────────────────────────────────────────────

    def _is_token_valid(self) -> bool:
        return (
            self._access_token is not None
            and time.time() < self._expires_at - _EXPIRY_MARGIN_SECONDS
        )

    def _refresh(self) -> None:
        """Exchange the refresh token for a new access token."""
        logger.info("Refreshing GBP OAuth access token …")

        resp = requests.post(
            _TOKEN_ENDPOINT,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error("OAuth token refresh failed: %s %s", resp.status_code, resp.text[:500])
            raise OAuthRefreshError(
                f"Token refresh failed ({resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._expires_at = time.time() + expires_in

        logger.info("GBP OAuth token refreshed, expires in %ds", expires_in)


class OAuthRefreshError(Exception):
    """Raised when the OAuth token refresh request fails."""


# ── module-level singleton (lazy) ────────────────────────────

_manager: GoogleOAuthManager | None = None


def get_oauth_manager() -> GoogleOAuthManager:
    """
    Return the module-level GoogleOAuthManager singleton.

    Lazily created on first call so that missing env vars don't
    crash the process at import time (important for tests and
    for deployments that don't use GBP).
    """
    global _manager
    if _manager is None:
        from config import (
            GBP_OAUTH_CLIENT_ID,
            GBP_OAUTH_CLIENT_SECRET,
            GBP_REFRESH_TOKEN,
        )
        _manager = GoogleOAuthManager(
            client_id=GBP_OAUTH_CLIENT_ID,
            client_secret=GBP_OAUTH_CLIENT_SECRET,
            refresh_token=GBP_REFRESH_TOKEN,
        )
    return _manager


def reset_oauth_manager() -> None:
    """Reset the singleton (useful for tests)."""
    global _manager
    _manager = None
```

## 6. `web_app.py` — כניסה ונקודות הקצה של הדרייב

**`web_app.py`** · תיעוד המודול, ייבוא, לוגים ויצירת `app` · שורות 1–100

> ראו הערה 11 למעלה: חלק מהייבוא לא קשור לבורר.

```python
"""
web_app.py — פאנל ווב לניהול פוסטים ברשתות חברתיות

Flask app שמתחבר ל-Google Sheets ו-Google Drive,
ומספק ממשק פשוט ללקוחה לניהול הפוסטים.
"""

import hashlib
import hmac
import logging
import os
import re
import sys
import threading
import requests as http_requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from dateutil import parser as dtparser
from flask import Flask, Response, jsonify, render_template, request

from config_constants import (
    TZ_IL,
    COL_ID,
    COL_STATUS,
    COL_NETWORK,
    COL_POST_TYPE,
    COL_PUBLISH_AT,
    COL_CAPTION,
    COL_CAPTION_IG,
    COL_CAPTION_FB,
    COL_CAPTION_GBP,
    COL_CAPTION_LI,
    COL_LI_AUTHOR_URN,
    COL_GBP_POST_TYPE,
    COL_CTA_TYPE,
    COL_CTA_URL,
    COL_GOOGLE_LOCATION_ID,
    COL_HASHTAGS,
    COL_FIRST_COMMENT,
    COL_DRIVE_FILE_ID,
    COL_COVER_FILE_ID,
    COL_CLOUDINARY_URL,
    COL_SOURCE,
    COL_RESULT,
    COL_ERROR,
    COL_RETRY_COUNT,
    COL_LOCKED_AT,
    COL_PROCESSING_BY,
    COL_PUBLISHED_CHANNELS,
    COL_FAILED_CHANNELS,
    STATUS_DRAFT,
    STATUS_READY,
    STATUS_POSTED,
    STATUS_PARTIAL,
    STATUS_ERROR,
    STATUS_PROCESSING,
    NETWORK_IG,
    NETWORK_FB,
    NETWORK_GBP,
    NETWORK_LI,
    NETWORK_BOTH,
    NETWORK_IG_GBP,
    NETWORK_FB_GBP,
    NETWORK_ALL_THREE,
    NETWORK_ALL,
    VALID_NETWORKS,
    POST_TYPE_FEED,
    POST_TYPE_REELS,
    GBP_POST_TYPE_STANDARD,
    SHEET_COLUMNS,
)
from google_api import (
    sheets_read_all_rows,
    sheets_read_row,
    sheets_read_header_and_row,
    sheets_update_cells,
    sheets_append_row,
    sheets_delete_row,
    col_letter_from_header,
    drive_list_folder,
    drive_get_media_metadata,
    get_drive_service,
)
from media_processor import validate_media_from_metadata
from validator import extract_drive_file_id
from notifications import notify_health_issue, notify_meta_api_version_expiry, notify_meta_api_version_unknown
from rss_probe import get_cgroup_memory, get_rss_mb, release_memory

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("web-panel")

# ─── Flask App ───────────────────────────────────────────────
app = Flask(__name__)
```

**`web_app.py`** · אימות: `WEB_PANEL_SECRET`, ‏`_COOKIE_TOKEN`, ‏`_check_auth`, ‏`_set_auth_cookie`, ‏`_login_page` · שורות 102–259

```python
# ─── Authentication ──────────────────────────────────────────
# Set WEB_PANEL_SECRET to require a bearer token / query param for all requests.
# Without it the panel is fully open — do NOT deploy without setting this.
WEB_PANEL_SECRET = os.environ.get("WEB_PANEL_SECRET", "")
WEB_PANEL_DEV_SECRET = os.environ.get("WEB_PANEL_DEV_SECRET", "")

if not WEB_PANEL_SECRET:
    logger.warning(
        "WEB_PANEL_SECRET is not set — the web panel has NO authentication! "
        "Set this env var before deploying to production."
    )

# Derive a cookie token via HMAC so the raw secret is never stored in the browser.
_COOKIE_TOKEN = (
    hmac.new(
        WEB_PANEL_SECRET.encode(), b"panel_cookie", hashlib.sha256
    ).hexdigest()
    if WEB_PANEL_SECRET
    else ""
)
_DEV_COOKIE_TOKEN = (
    hmac.new(
        WEB_PANEL_DEV_SECRET.encode(), b"panel_dev_cookie", hashlib.sha256
    ).hexdigest()
    if WEB_PANEL_DEV_SECRET
    else ""
)


@app.before_request
def _check_auth():
    """Verify every request carries a valid secret (header, query-param, or cookie)."""
    if not WEB_PANEL_SECRET:
        return  # auth disabled (dev mode)

    # Static assets, health check, and privacy policy are public
    if (
        request.path.startswith("/static/")
        or request.path == "/api/health"
        or request.path == "/privacy"
    ):
        return

    # Accept: Authorization: Bearer <secret>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and hmac.compare_digest(
        auth_header[7:], WEB_PANEL_SECRET
    ):
        return

    # Accept: ?token=<secret>  (useful for browser bookmarks)
    token_param = request.args.get("token", "")
    if token_param and hmac.compare_digest(token_param, WEB_PANEL_SECRET):
        return

    # Accept: dev secret via ?token=<dev_secret>
    if WEB_PANEL_DEV_SECRET and token_param and hmac.compare_digest(token_param, WEB_PANEL_DEV_SECRET):
        return

    # Accept: HMAC cookie set by a previous token= visit
    if request.cookies.get("panel_token") and hmac.compare_digest(
        request.cookies["panel_token"], _COOKIE_TOKEN
    ):
        return

    # Accept: dev cookie
    if _DEV_COOKIE_TOKEN and request.cookies.get("panel_dev") and hmac.compare_digest(
        request.cookies["panel_dev"], _DEV_COOKIE_TOKEN
    ):
        return

    # Not authenticated — show login page for browser requests, JSON for API
    if request.path == "/" or not request.path.startswith("/api/"):
        return _login_page(), 401
    return jsonify({"error": "Unauthorized"}), 401


@app.after_request
def _set_auth_cookie(response):
    """When the user authenticates via ?token=, persist an HMAC-derived cookie."""
    token_param = request.args.get("token", "")
    if not token_param:
        return response

    is_https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    cookie_opts = dict(httponly=True, secure=is_https, samesite="Lax", max_age=60 * 60 * 24 * 30)

    # Dev secret → set dev cookie
    if (
        WEB_PANEL_DEV_SECRET
        and hmac.compare_digest(token_param, WEB_PANEL_DEV_SECRET)
        and not request.cookies.get("panel_dev")
    ):
        response.set_cookie("panel_dev", _DEV_COOKIE_TOKEN, **cookie_opts)
        # Also set regular panel_token so dev user has full access
        if not request.cookies.get("panel_token"):
            response.set_cookie("panel_token", _COOKIE_TOKEN, **cookie_opts)
    # Regular secret → set panel cookie
    elif (
        WEB_PANEL_SECRET
        and hmac.compare_digest(token_param, WEB_PANEL_SECRET)
        and not request.cookies.get("panel_token")
    ):
        response.set_cookie("panel_token", _COOKIE_TOKEN, **cookie_opts)

    return response


def _login_page() -> str:
    """Simple Hebrew login page shown when no valid auth is present."""
    return """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Social Publisher — כניסה</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #e0e0e0;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .login-box { background: #252540; border: 1px solid #3a3a5c; border-radius: 12px;
                 padding: 40px; max-width: 380px; width: 100%; text-align: center; }
    .login-box h1 { font-size: 22px; margin-bottom: 8px; }
    .login-box p { font-size: 14px; color: #999; margin-bottom: 24px; }
    .login-box input { width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #3a3a5c;
                       background: #1a1a2e; color: #e0e0e0; font-size: 15px; margin-bottom: 16px;
                       direction: ltr; text-align: center; }
    .login-box input:focus { outline: none; border-color: #6c63ff; }
    .login-box button { width: 100%; padding: 10px; border-radius: 8px; border: none;
                        background: #6c63ff; color: white; font-size: 15px; cursor: pointer; }
    .login-box button:hover { background: #5a52d5; }
    .error { color: #ff6b6b; font-size: 13px; margin-bottom: 12px; display: none; }
  </style>
</head>
<body>
  <div class="login-box">
    <h1>Social Publisher</h1>
    <p>הזיני את הסיסמה כדי להיכנס לפאנל</p>
    <div class="error" id="err">סיסמה שגויה</div>
    <form onsubmit="go(event)">
      <input type="password" id="pw" placeholder="סיסמה" autofocus>
      <button type="submit">כניסה</button>
    </form>
  </div>
  <script>
    function go(e) {
      e.preventDefault();
      const pw = document.getElementById('pw').value;
      if (!pw) return;
      window.location.href = '/?token=' + encodeURIComponent(pw);
    }
    // If we arrived with a wrong token, show error
    if (location.search.includes('token=')) {
      document.getElementById('err').style.display = 'block';
    }
  </script>
</body>
</html>"""
```

**`web_app.py`** · `DRIVE_FOLDER_ID` · שורות 262–263

```python
# Drive folder ID (root folder for media files)
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
```

**`web_app.py`** · `index` — מגיש את `templates/index.html` · שורות 266–272

```python
# ═══════════════════════════════════════════════════════════════
#  Pages
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")
```

**`web_app.py`** · `_is_folder_within_root`, ‏`_is_known_drive_file`, ‏`api_drive_thumbnail`, ‏`api_drive_files` · שורות 1030–1206

```python
# ═══════════════════════════════════════════════════════════════
#  API — Google Drive
# ═══════════════════════════════════════════════════════════════

def _is_folder_within_root(folder_id: str, root_id: str, max_depth: int = 10) -> bool:
    """
    Verify folder_id is the root or a descendant of root_id
    by walking up the parent chain. Prevents folder traversal attacks.
    """
    if folder_id == root_id:
        return True

    svc = get_drive_service()
    current = folder_id
    for _ in range(max_depth):
        try:
            meta = svc.files().get(fileId=current, fields="parents", supportsAllDrives=True).execute()
            parents = meta.get("parents", [])
            if not parents:
                return False
            if root_id in parents:
                return True
            current = parents[0]
        except Exception:
            return False
    return False


def _is_known_drive_file(file_id: str) -> bool:
    """Check if file_id exists in the Google Sheet's drive_file_id column.
    Handles comma-separated IDs for carousel posts."""
    try:
        header, rows = sheets_read_all_rows()
        if not header:
            return False
        idx = header.index(COL_DRIVE_FILE_ID)
        return any(
            idx < len(row) and file_id in {fid.strip() for fid in row[idx].split(",")}
            for row in rows
        )
    except (ValueError, Exception):
        return False


@app.route("/api/drive/thumbnail/<file_id>", methods=["GET"])
def api_drive_thumbnail(file_id):
    """מחזיר תמונה ממוזערת של קובץ מ-Drive (proxy)."""
    # Debug mode: only available via FLASK_DEBUG env var (not exposed to regular users)
    debug = (
        request.args.get("debug") == "1"
        and os.environ.get("FLASK_DEBUG", "").lower() == "true"
    )
    try:
        if not file_id or len(file_id) > 120 or not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
            if debug:
                return jsonify({"step": "validate", "error": "Invalid file_id format"})
            return Response(status=400)

        if not DRIVE_FOLDER_ID:
            if debug:
                return jsonify({"step": "config", "error": "GOOGLE_DRIVE_FOLDER_ID not set"})
            return Response(status=404)

        # Verify the file belongs to the configured root folder tree
        svc = get_drive_service()
        meta = svc.files().get(fileId=file_id, fields="thumbnailLink,parents", supportsAllDrives=True).execute()

        parents = meta.get("parents", [])

        # Verify file belongs to the configured root folder tree.
        # Some Drive configs return empty parents — in that case, fall back
        # to checking if the file_id exists in our Google Sheet (since
        # file_id is a user-controlled URL parameter, not just Sheet data).
        if parents:
            if not any(_is_folder_within_root(p, DRIVE_FOLDER_ID) for p in parents):
                logger.warning(f"Thumbnail denied: file {file_id} not within root folder")
                if debug:
                    folder_check = {p: _is_folder_within_root(p, DRIVE_FOLDER_ID) for p in parents}
                    return jsonify({"step": "folder_check", "error": "File not within root folder",
                                    "parents": parents, "root": DRIVE_FOLDER_ID, "checks": folder_check})
                return Response(status=403)
        else:
            if not _is_known_drive_file(file_id):
                logger.warning(f"Thumbnail denied: file {file_id} has no parents and is not in Sheet")
                if debug:
                    return jsonify({"step": "folder_check", "error": "File not in Sheet and no parents to verify",
                                    "parents": [], "root": DRIVE_FOLDER_ID})
                return Response(status=403)

        thumb_url = meta.get("thumbnailLink")
        if not thumb_url:
            logger.debug(f"No thumbnailLink for file {file_id}")
            if debug:
                return jsonify({"step": "thumbnailLink", "error": "Google returned no thumbnailLink",
                                "meta_keys": list(meta.keys())})
            return Response(status=404)

        # Google's thumbnailLink ends with =s220 (default size).
        # Replace with a larger size when requested for the lightbox.
        size = request.args.get("size", "small")
        if size == "large":
            thumb_url = re.sub(r'=s\d+$', '=s1200', thumb_url)

        if debug:
            return jsonify({"step": "ready", "thumbnailLink": thumb_url[:80] + "...",
                            "will_fetch": True})

        # Fetch thumbnail with service-account auth via requests (thread-safe).
        # svc._http.credentials is auto-refreshed by prior API calls above.
        MAX_THUMB_BYTES = 5 * 1024 * 1024  # 5 MB safety cap
        creds = svc._http.credentials
        if not creds.token:
            import google.auth.transport.requests as gauth_transport
            creds.refresh(gauth_transport.Request())

        thumb_resp = http_requests.get(
            thumb_url,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
            stream=True,
        )

        try:
            if thumb_resp.status_code != 200:
                logger.warning(f"Thumbnail fetch failed for {file_id}: HTTP {thumb_resp.status_code}")
                return Response(status=502)

            data = thumb_resp.raw.read(MAX_THUMB_BYTES + 1, decode_content=True)

            if len(data) > MAX_THUMB_BYTES:
                return Response(status=413)

            content_type = thumb_resp.headers.get("Content-Type", "image/png")
        finally:
            thumb_resp.close()

        # Only proxy image MIME types to prevent serving active content (XSS)
        if not content_type.startswith("image/"):
            logger.warning(f"Thumbnail for {file_id} returned non-image type: {content_type}")
            return Response(status=502)

        return Response(
            data,
            mimetype=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    except Exception as e:
        logger.error(f"Thumbnail error for {file_id}: {e}", exc_info=True)
        return Response(status=404)
    finally:
        release_memory()


@app.route("/api/drive/files", methods=["GET"])
def api_drive_files():
    """מחזיר קבצים מתיקיית Drive."""
    try:
        folder_id = request.args.get("folder_id", DRIVE_FOLDER_ID)
        if not folder_id:
            return jsonify({"error": "No folder ID configured"}), 400

        if not DRIVE_FOLDER_ID:
            return jsonify({"error": "No root folder configured"}), 400

        # Validate the folder is within the allowed root
        if not _is_folder_within_root(folder_id, DRIVE_FOLDER_ID):
            return jsonify({"error": "Access denied: folder outside allowed scope"}), 403

        page_token = request.args.get("page_token")
        result = drive_list_folder(folder_id, page_token)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error listing Drive files: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
```

**`web_app.py`** · `api_config` — מעביר את `driveFolderId` לפרונט · שורות 1516–1535

> תלות: `openDriveBrowser` קורא את `config.driveFolderId` שנטען מכאן.

```python
# ═══════════════════════════════════════════════════════════════
#  API — Config (public, non-sensitive)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def api_config():
    """מחזיר הגדרות ציבוריות לפרונטאנד."""
    is_dev = (
        _DEV_COOKIE_TOKEN
        and request.cookies.get("panel_dev")
        and hmac.compare_digest(request.cookies["panel_dev"], _DEV_COOKIE_TOKEN)
    )
    return jsonify({
        "driveFolderId": DRIVE_FOLDER_ID,
        "columns": SHEET_COLUMNS,
        "statuses": [STATUS_DRAFT, STATUS_READY, STATUS_PROCESSING, STATUS_POSTED, STATUS_PARTIAL, STATUS_ERROR],
        "networks": sorted(VALID_NETWORKS),
        "postTypes": [POST_TYPE_FEED, POST_TYPE_REELS],
        "isDev": bool(is_dev),
    })
```

## 7. `rss_probe.py` — `release_memory` (תלות של `api_drive_thumbnail`)

**`rss_probe.py`** · תיעוד המודול, ייבוא, `logger` ו-`_libc_cache` · שורות 1–20

```python
"""
rss_probe.py — memory probing & release utilities.

Used by the web panel to surface a memory-usage banner and offer a manual
restart when usage approaches Render's 512MB limit. Also exposes
release_memory() so request handlers that allocate large buffers (Pillow,
googleapiclient response bodies) can return memory to the OS instead of
letting it sit in glibc's heap until the worker is recycled.
"""

import gc
import logging
import os

logger = logging.getLogger("rss-probe")

# Cache libc lookup so we don't repeat ctypes.CDLL on every release_memory()
# call. If loading fails once it will always fail in this process; remembering
# that lets us short-circuit cheaply.
_libc_cache: "object | bool | None" = None
```

**`rss_probe.py`** · `release_memory` · שורות 78–117

```python
def release_memory() -> None:
    """
    Run gc.collect() + libc.malloc_trim(0) to return freed memory to the OS.

    Glibc holds onto freed allocations in its heap by default, which causes
    RSS to drift upward even after Python objects are collected. malloc_trim
    forces glibc to release those back to the kernel; combined with
    MALLOC_TRIM_THRESHOLD_ in the Dockerfile this keeps the worker comfortably
    under Render's 512MB limit between max-requests recycles.
    """
    global _libc_cache

    try:
        gc.collect()
    except Exception as e:
        logger.debug(f"gc.collect failed: {e}")

    if _libc_cache is False:
        return

    if _libc_cache is None:
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim.argtypes = [ctypes.c_size_t]
            libc.malloc_trim.restype = ctypes.c_int
            _libc_cache = libc
        except Exception as e:
            # Broad catch on purpose: this runs in a finally block from Flask
            # handlers, so any propagated error would mask the original response
            # with a 500. Narrower catches also miss ImportError on minimal
            # images and would re-attempt loading on every call.
            logger.debug(f"libc.malloc_trim unavailable: {e}")
            _libc_cache = False
            return

    try:
        _libc_cache.malloc_trim(0)
    except Exception as e:
        logger.debug(f"malloc_trim failed: {e}")
```

## 8. `static/app.js` — בורר הדרייב והעזרים

**`static/app.js`** · משתנה המצב `config` · שורה 8

```javascript
let config = {};
```

**`static/app.js`** · משתני המצב של הבורר · שורות 22–26

```javascript
// Drive browser state
let driveStack = [];       // [{folderId, name}] for breadcrumb
let selectedDriveFile = null;
let selectedDriveFiles = [];   // [{id, name}] for multi-select (carousel)
let driveBrowserTarget = 'main';  // 'main' (drive_file_id) | 'cover' (cover_drive_file_id)
```

**`static/app.js`** · אתחול (`DOMContentLoaded`) · שורות 40–47

> מועתק כמו שהוא: קורא גם לפונקציות של הפאנל שלא נכללו כאן (`loadPosts`, `startStatusPolling`, `startMemoryPolling`, `openFromQueryParam`). מה שהבורר צריך ממנו הוא רק `loadConfig`.

```javascript
// ─── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadConfig();
  await loadPosts();
  startStatusPolling();
  startMemoryPolling();
  openFromQueryParam();
});
```

**`static/app.js`** · `loadConfig` · שורות 73–80

```javascript
async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    config = await resp.json();
  } catch (e) {
    console.error('Failed to load config:', e);
  }
}
```

**`static/app.js`** · קטע ה-Drive Browser המלא: `openDriveBrowser`, ‏`openCoverDriveBrowser`, ‏`closeDriveBrowser`, ‏`loadDriveFolder`, ‏`navigateDriveFolder`, ‏`navigateDriveBreadcrumb`, ‏`renderDriveBreadcrumb`, ‏`_isMultiSelectAllowed`, ‏`selectDriveFile`, ‏`_updateDriveConfirmBtn`, ‏`confirmDriveSelection`, ‏`clearDriveFile`, ‏`clearCoverFile`, ‏`toggleManualCoverId`, ‏`toggleManualFileId` · שורות 1043–1287

```javascript
// ═══════════════════════════════════════════════════════════════
//  Drive Browser
// ═══════════════════════════════════════════════════════════════

function openDriveBrowser() {
  if (!config.driveFolderId) {
    showToast('לא הוגדרה תיקיית Drive. יש להגדיר GOOGLE_DRIVE_FOLDER_ID.', 'error');
    return;
  }

  driveBrowserTarget = 'main';
  selectedDriveFile = null;
  selectedDriveFiles = [];
  driveStack = [{ folderId: config.driveFolderId, name: 'תיקייה ראשית' }];
  _updateDriveConfirmBtn();
  openModal('drive-modal');
  loadDriveFolder(config.driveFolderId);
}

function openCoverDriveBrowser() {
  if (!config.driveFolderId) {
    showToast('לא הוגדרה תיקיית Drive. יש להגדיר GOOGLE_DRIVE_FOLDER_ID.', 'error');
    return;
  }

  driveBrowserTarget = 'cover';
  selectedDriveFile = null;
  selectedDriveFiles = [];
  driveStack = [{ folderId: config.driveFolderId, name: 'תיקייה ראשית' }];
  _updateDriveConfirmBtn();
  openModal('drive-modal');
  loadDriveFolder(config.driveFolderId);
}

function closeDriveBrowser() {
  closeModal('drive-modal');
}

async function loadDriveFolder(folderId) {
  const browser = document.getElementById('drive-browser');
  const loading = document.getElementById('drive-loading');
  const empty = document.getElementById('drive-empty');

  browser.innerHTML = '';
  showElement('drive-loading');
  hideElement('drive-empty');

  try {
    const resp = await fetch(`/api/drive/files?folder_id=${encodeURIComponent(folderId)}`);
    const data = await resp.json();

    if (data.error) {
      showToast(data.error, 'error');
      return;
    }

    hideElement('drive-loading');
    const files = data.files || [];

    if (files.length === 0) {
      showElement('drive-empty');
      return;
    }

    // Separate folders and files
    const folders = files.filter(f => f.mimeType === 'application/vnd.google-apps.folder');
    const mediaFiles = files.filter(f => f.mimeType !== 'application/vnd.google-apps.folder');

    // Render folders first
    folders.forEach(f => {
      const el = document.createElement('div');
      el.className = 'drive-file drive-folder';
      el.dataset.folderId = f.id;
      el.dataset.folderName = f.name;
      el.innerHTML = `
        <div class="drive-file-icon">&#128193;</div>
        <div class="drive-file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
      `;
      el.addEventListener('dblclick', () => navigateDriveFolder(f.id, f.name));
      browser.appendChild(el);
    });

    // Render files
    mediaFiles.forEach(f => {
      const el = document.createElement('div');
      el.className = 'drive-file';
      el.dataset.fileId = f.id;
      el.dataset.fileName = f.name;
      const icon = getFileIcon(f.mimeType);
      const thumb = f.thumbnailLink
        ? `<img src="${escapeHtml(f.thumbnailLink)}" alt="${escapeHtml(f.name)}" loading="lazy">`
        : icon;
      el.innerHTML = `
        <div class="drive-file-icon">${thumb}</div>
        <div class="drive-file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
      `;
      el.addEventListener('click', () => selectDriveFile(el, f.id, f.name));
      browser.appendChild(el);
    });

    renderDriveBreadcrumb();

  } catch (e) {
    hideElement('drive-loading');
    showToast('שגיאה בטעינת קבצים מ-Drive', 'error');
    console.error(e);
  }
}

function navigateDriveFolder(folderId, name) {
  driveStack.push({ folderId, name });
  selectedDriveFile = null;
  selectedDriveFiles = [];
  _updateDriveConfirmBtn();
  loadDriveFolder(folderId);
}

function navigateDriveBreadcrumb(index) {
  driveStack = driveStack.slice(0, index + 1);
  selectedDriveFile = null;
  selectedDriveFiles = [];
  _updateDriveConfirmBtn();
  loadDriveFolder(driveStack[index].folderId);
}

function renderDriveBreadcrumb() {
  const el = document.getElementById('drive-breadcrumb');
  el.innerHTML = driveStack.map((item, i) => {
    const isLast = i === driveStack.length - 1;
    const link = isLast
      ? `<span style="color:var(--color-text-primary)">${escapeHtml(item.name)}</span>`
      : `<span class="drive-breadcrumb-item" onclick="navigateDriveBreadcrumb(${i})">${escapeHtml(item.name)}</span>`;
    const sep = i < driveStack.length - 1 ? '<span class="drive-breadcrumb-separator">/</span>' : '';
    return link + sep;
  }).join('');
}

function _isMultiSelectAllowed() {
  // Cover image is always a single file
  if (driveBrowserTarget === 'cover') return false;
  const channels = getSelectedChannels();
  // Multi-file carousel only supported for IG-only
  return channels.length === 1 && channels[0] === 'IG';
}

function selectDriveFile(el, fileId, fileName) {
  const multiAllowed = _isMultiSelectAllowed();

  const idx = selectedDriveFiles.findIndex(f => f.id === fileId);
  if (idx !== -1) {
    // Deselect
    selectedDriveFiles.splice(idx, 1);
    el.classList.remove('selected');
  } else {
    if (!multiAllowed && selectedDriveFiles.length >= 1) {
      // FB only — single select: replace previous selection
      document.querySelectorAll('.drive-file.selected').forEach(e => e.classList.remove('selected'));
      selectedDriveFiles = [];
    }
    if (selectedDriveFiles.length >= 10) {
      showToast('ניתן לבחור עד 10 קבצים לקרוסלה', 'error');
      return;
    }
    selectedDriveFiles.push({ id: fileId, name: fileName });
    el.classList.add('selected');
  }
  // Keep backward-compat for single file
  selectedDriveFile = selectedDriveFiles.length === 1 ? selectedDriveFiles[0] : null;
  _updateDriveConfirmBtn();
}

function _updateDriveConfirmBtn() {
  const btn = document.getElementById('btn-confirm-drive');
  const count = selectedDriveFiles.length;
  btn.disabled = count === 0;
  if (count > 1) {
    btn.textContent = `בחירת ${count} קבצים`;
  } else {
    btn.textContent = 'בחירה';
  }
}

function confirmDriveSelection() {
  if (selectedDriveFiles.length === 0) return;

  if (driveBrowserTarget === 'cover') {
    const f = selectedDriveFiles[0];
    document.getElementById('form-cover-drive-file-id').value = f.id;
    document.getElementById('form-cover-drive-file-id-manual').value = '';
    document.getElementById('selected-cover-name').textContent = f.name;
    showElement('cover-file-display');
    hideElement('form-cover-drive-file-id-manual');
    closeDriveBrowser();
    return;
  }

  const ids = selectedDriveFiles.map(f => f.id).join(',');
  const names = selectedDriveFiles.map(f => f.name);
  const displayName = names.length === 1
    ? names[0]
    : `${names.length} קבצים: ${names.join(', ')}`;

  document.getElementById('form-drive-file-id').value = ids;
  document.getElementById('selected-file-name').textContent = displayName;
  showElement('drive-file-display');
  hideElement('form-drive-file-id-manual');

  closeDriveBrowser();
}

function clearDriveFile() {
  document.getElementById('form-drive-file-id').value = '';
  document.getElementById('form-drive-file-id-manual').value = '';
  selectedDriveFiles = [];
  selectedDriveFile = null;
  hideElement('drive-file-display');
}

function clearCoverFile() {
  const idEl = document.getElementById('form-cover-drive-file-id');
  const manualEl = document.getElementById('form-cover-drive-file-id-manual');
  if (idEl) idEl.value = '';
  if (manualEl) {
    manualEl.value = '';
    manualEl.classList.add('hidden');
  }
  hideElement('cover-file-display');
}

function toggleManualCoverId() {
  const el = document.getElementById('form-cover-drive-file-id-manual');
  if (!el) return;
  el.classList.toggle('hidden');
  if (!el.classList.contains('hidden')) {
    el.focus();
  }
}

function toggleManualFileId() {
  const el = document.getElementById('form-drive-file-id-manual');
  el.classList.toggle('hidden');
  if (!el.classList.contains('hidden')) {
    el.focus();
  }
}
```

### 8א. עזרים שהבורר משתמש בהם

**`static/app.js`** · `getSelectedChannels` · שורות 528–535

> תלות של `_isMultiSelectAllowed`.

```javascript
function getSelectedChannels() {
  const channels = [];
  if (document.getElementById('form-ch-ig').checked) channels.push('IG');
  if (document.getElementById('form-ch-fb').checked) channels.push('FB');
  if (document.getElementById('form-ch-gbp').checked) channels.push('GBP');
  if (document.getElementById('form-ch-li').checked) channels.push('LI');
  return channels;
}
```

**`static/app.js`** · UI Helpers: `openModal`, ‏`closeModal`, סגירה בלחיצה על הרקע, סגירה ב-Escape, ‏`showElement`, ‏`hideElement`, ‏`showToast` · שורות 1528–1590

```javascript
// ═══════════════════════════════════════════════════════════════
//  UI Helpers
// ═══════════════════════════════════════════════════════════════

function openModal(id) {
  document.getElementById(id).classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
  document.body.style.overflow = '';
}

// Close modal on backdrop click (except post-modal to prevent accidental data loss)
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-backdrop') && e.target.classList.contains('active')) {
    if (e.target.id === 'post-modal') return;
    e.target.classList.remove('active');
    document.body.style.overflow = '';
  }
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // Close lightbox if open
    const lightbox = document.getElementById('image-lightbox');
    if (lightbox && lightbox.classList.contains('active')) {
      closeLightbox();
      return;
    }
    let closedAny = false;
    document.querySelectorAll('.modal-backdrop.active').forEach(el => {
      if (el.id === 'post-modal') return;
      el.classList.remove('active');
      closedAny = true;
    });
    if (closedAny) document.body.style.overflow = '';
  }
});

function showElement(id) {
  document.getElementById(id).classList.remove('hidden');
}

function hideElement(id) {
  document.getElementById(id).classList.add('hidden');
}

// ─── Toast Notifications ─────────────────────────────────────
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 200);
  }, 4000);
}
```

**`static/app.js`** · `closeLightbox` · שורות 1018–1023

> תלות של מאזין ה-Escape שבקטע הקודם.

```javascript
function closeLightbox() {
  const lightbox = document.getElementById('image-lightbox');
  lightbox.classList.remove('active');
  document.getElementById('lightbox-img').src = '';
  document.body.style.overflow = '';
}
```

**`static/app.js`** · `escapeHtml` · שורות 1754–1762

```javascript
function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
```

**`static/app.js`** · `getFileIcon` · שורות 1764–1770

```javascript
function getFileIcon(mimeType) {
  if (!mimeType) return '&#128196;';
  if (mimeType.startsWith('image/')) return '&#128247;';
  if (mimeType.startsWith('video/')) return '&#127909;';
  if (mimeType === 'application/vnd.google-apps.folder') return '&#128193;';
  return '&#128196;';
}
```

## 9. `templates/index.html` — מודאל הבורר והשדות שהוא כותב אליהם

**`templates/index.html`** · ראש המסמך (RTL וקישור ל-`style.css`) · שורות 1–9

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Social Publisher</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
```

**`templates/index.html`** · Drive Browser Modal · שורות 423–448

```html
<!-- ═══ Drive Browser Modal ═══ -->
<div class="modal-backdrop" id="drive-modal">
  <div class="modal modal-wide">
    <div class="modal-header">
      <h2 class="modal-title">בחירת קובץ מ-Google Drive</h2>
      <button class="modal-close" onclick="closeDriveBrowser()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="drive-breadcrumb" id="drive-breadcrumb"></div>
      <div id="drive-loading" class="loading-overlay hidden">
        <div class="spinner"></div>
      </div>
      <div id="drive-empty" class="empty-state hidden">
        <div class="empty-state-icon">&#128193;</div>
        <div class="empty-state-text">התיקייה ריקה</div>
      </div>
      <div class="drive-browser" id="drive-browser"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-primary btn-md" onclick="confirmDriveSelection()" id="btn-confirm-drive" disabled>
        בחירה
      </button>
      <button class="btn btn-ghost btn-md" onclick="closeDriveBrowser()">ביטול</button>
    </div>
  </div>
</div>
```

### 9א. תלויות ב-HTML

**`templates/index.html`** · שדות המדיה בטופס הפוסט (הכפתורים שפותחים את הבורר והשדות ש-`confirmDriveSelection` כותב אליהם) · שורות 372–414

```html
      <div class="form-group" id="media-group">
        <label class="form-label">קובץ מדיה</label>
        <div id="drive-file-display" class="hidden">
          <div class="drive-file-id-display">
            <span class="file-name" id="selected-file-name"></span>
            <button class="btn btn-ghost btn-sm" onclick="clearDriveFile()">&times;</button>
          </div>
        </div>
        <input type="hidden" id="form-drive-file-id">
        <div class="flex gap-sm mt-sm">
          <button class="btn btn-outline btn-md" onclick="openDriveBrowser()" id="btn-browse-drive">
            &#128193; בחירה מ-Drive
          </button>
          <button class="btn btn-ghost btn-sm" onclick="toggleManualFileId()" id="btn-manual-id">
            הזנה ידנית
          </button>
        </div>
        <input type="text" class="form-input hidden mt-sm" id="form-drive-file-id-manual"
               placeholder="הדביקי כאן את ה-File ID מ-Drive"
               oninput="document.getElementById('form-drive-file-id').value = this.value">
      </div>
      <div class="form-group hidden" id="cover-group">
        <label class="form-label">תמונת כיסוי לריל <span class="form-hint">(אופציונלי)</span></label>
        <div id="cover-file-display" class="hidden">
          <div class="drive-file-id-display">
            <span class="file-name" id="selected-cover-name"></span>
            <button class="btn btn-ghost btn-sm" onclick="clearCoverFile()">&times;</button>
          </div>
        </div>
        <input type="hidden" id="form-cover-drive-file-id">
        <div class="flex gap-sm mt-sm">
          <button class="btn btn-outline btn-md" onclick="openCoverDriveBrowser()" id="btn-browse-cover-drive">
            &#128193; בחירה מ-Drive
          </button>
          <button class="btn btn-ghost btn-sm" onclick="toggleManualCoverId()" id="btn-manual-cover-id">
            הזנה ידנית
          </button>
        </div>
        <input type="text" class="form-input hidden mt-sm" id="form-cover-drive-file-id-manual"
               placeholder="הדביקי כאן את ה-File ID של תמונת הכיסוי" dir="ltr"
               oninput="document.getElementById('form-cover-drive-file-id').value = this.value">
        <div class="form-hint">כל פורמט תמונה (JPEG/PNG/WebP/GIF/BMP) — יומר אוטומטית. נדרש: יחס 9:16, גובה ≥ 1280px. אם פסול — הפרסום יידחה.</div>
      </div>
```

**`templates/index.html`** · תיבות הסימון של הערוצים · שורות 234–254

> תלות של `getSelectedChannels`.

```html
        <div class="form-group">
          <label class="form-label">ערוצים</label>
          <div class="channel-checkboxes">
            <label class="channel-checkbox">
              <input type="checkbox" id="form-ch-ig" onchange="onChannelChange()">
              <span class="channel-checkbox-label">IG</span>
            </label>
            <label class="channel-checkbox">
              <input type="checkbox" id="form-ch-fb" onchange="onChannelChange()">
              <span class="channel-checkbox-label">FB</span>
            </label>
            <label class="channel-checkbox">
              <input type="checkbox" id="form-ch-gbp" onchange="onChannelChange()">
              <span class="channel-checkbox-label">GBP</span>
            </label>
            <label class="channel-checkbox">
              <input type="checkbox" id="form-ch-li" onchange="onChannelChange()">
              <span class="channel-checkbox-label">LI</span>
            </label>
          </div>
        </div>
```

**`templates/index.html`** · מיכל ההודעות (`showToast`) · שורות 479–480

```html
<!-- ═══ Toast Container ═══ -->
<div class="toast-container" id="toast-container"></div>
```

**`templates/index.html`** · Image Lightbox · שורות 501–504

> תלות של `closeLightbox`.

```html
<!-- ═══ Image Lightbox ═══ -->
<div class="lightbox" id="image-lightbox" onclick="closeLightbox()">
  <img id="lightbox-img" src="" alt="" onclick="event.stopPropagation()">
</div>
```

**`templates/index.html`** · טעינת `app.js` · שורות 509–511

```html
<script src="/static/app.js"></script>
</body>
</html>
```

## 10. `static/style.css` — עיצוב הבורר, המודאל והכפתורים

**`static/style.css`** · גופן, משתני CSS (`:root`), איפוס ובסיס · שורות 1–110

```css
/* ═══════════════════════════════════════════════════════════════
   Social Publisher — Design System
   Based on design spec: dark mode, RTL, Rubik font, indigo accent
   ═══════════════════════════════════════════════════════════════ */

/* ─── Fonts ─────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500;600;700&display=swap');

/* ─── CSS Variables ─────────────────────────────────────────── */
:root {
  /* Colors — oklch palette */
  --color-bg-primary: oklch(0.13 0.015 270);
  --color-bg-card: oklch(0.17 0.015 270);
  --color-bg-input: oklch(0.25 0.015 270);
  --color-bg-hover: oklch(0.22 0.015 270);
  --color-bg-sidebar: oklch(0.15 0.015 270);
  --color-bg-modal: oklch(0.13 0.015 270 / 0.85);

  --color-text-primary: oklch(0.93 0.005 270);
  --color-text-secondary: oklch(0.65 0.015 270);
  --color-text-muted: oklch(0.50 0.015 270);

  --color-accent: oklch(0.637 0.237 275);
  --color-accent-hover: oklch(0.58 0.237 275);
  --color-accent-soft: oklch(0.637 0.237 275 / 0.10);
  --color-secondary: oklch(0.22 0.015 270);

  --color-success: oklch(0.65 0.2 120);
  --color-success-soft: oklch(0.65 0.2 120 / 0.15);
  --color-warning: oklch(0.75 0.15 85);
  --color-warning-soft: oklch(0.75 0.15 85 / 0.15);
  --color-error: oklch(0.65 0.2 25);
  --color-error-soft: oklch(0.65 0.2 25 / 0.15);
  --color-error-muted: oklch(0.60 0.12 25);
  --color-info: oklch(0.65 0.15 230);
  --color-info-soft: oklch(0.65 0.15 230 / 0.15);

  --color-border: oklch(0.28 0.015 270);
  --color-border-hover: oklch(0.38 0.015 270);

  /* Typography */
  --font-family: 'Rubik', 'Segoe UI', Arial, sans-serif;
  --font-size-xs: 12px;
  --font-size-sm: 13px;
  --font-size-base: 14px;
  --font-size-md: 16px;
  --font-size-lg: 18px;
  --font-size-xl: 22px;
  --font-size-2xl: 28px;

  /* Spacing */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 12px;
  --space-base: 16px;
  --space-lg: 20px;
  --space-xl: 24px;
  --space-2xl: 32px;
  --space-3xl: 48px;
  --space-4xl: 64px;

  /* Border radius */
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 10px;
  --radius-xl: 14px;
  --radius-full: 9999px;

  /* Transitions */
  --transition-fast: 150ms ease;
  --transition-normal: 200ms ease;
  --transition-slow: 300ms ease;

  /* Sidebar */
  --sidebar-width: 260px;
  --sidebar-collapsed: 60px;

  /* Z-index */
  --z-sidebar: 40;
  --z-modal-backdrop: 50;
  --z-modal: 60;
  --z-toast: 70;
}

/* ─── Reset & Base ──────────────────────────────────────────── */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  direction: rtl;
  font-size: 16px;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  font-family: var(--font-family);
  font-size: var(--font-size-base);
  font-weight: 400;
  color: var(--color-text-primary);
  background: var(--color-bg-primary);
  line-height: 1.5;
  min-height: 100vh;
  overflow-x: hidden;
}
```

**`static/style.css`** · כפתורים (`.btn-*`) · שורות 359–444

```css
/* ─── Buttons ───────────────────────────────────────────────── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  font-family: var(--font-family);
  font-weight: 500;
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
  text-decoration: none;
  line-height: 1;
}

.btn-sm {
  padding: 6px 12px;
  font-size: var(--font-size-xs);
  border-radius: var(--radius-sm);
}

.btn-md {
  padding: 8px 16px;
  font-size: var(--font-size-sm);
}

.btn-lg {
  padding: 12px 24px;
  font-size: var(--font-size-base);
}

.btn-primary {
  background: var(--color-accent);
  color: white;
}

.btn-primary:hover {
  background: var(--color-accent-hover);
  transform: scale(1.02);
}

.btn-secondary {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.btn-secondary:hover {
  background: var(--color-border);
}

.btn-outline {
  background: transparent;
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
}

.btn-outline:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-border-hover);
}

.btn-ghost {
  background: transparent;
  color: var(--color-text-secondary);
}

.btn-ghost:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.btn-destructive {
  background: var(--color-error);
  color: white;
}

.btn-destructive:hover {
  background: oklch(0.58 0.2 25);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
```

**`static/style.css`** · שדות טופס (`.form-group`, ‏`.form-label`, ‏`.form-input`) · שורות 446–491

> תלות של שדות המדיה בטופס.

```css
/* ─── Form Elements ─────────────────────────────────────────── */
.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}

.form-label {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-secondary);
}

.form-input,
.form-select,
.form-textarea {
  font-family: var(--font-family);
  font-size: var(--font-size-base);
  color: var(--color-text-primary);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  height: 40px;
  transition: border-color var(--transition-fast);
  width: 100%;
}

.form-textarea {
  height: auto;
  min-height: 80px;
  resize: vertical;
}

.form-input:focus,
.form-select:focus,
.form-textarea:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent-soft);
}

.form-input::placeholder,
.form-textarea::placeholder {
  color: var(--color-text-muted);
}
```

**`static/style.css`** · `.form-hint` · שורות 550–554

```css
.form-hint {
  font-weight: 400;
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}
```

**`static/style.css`** · מודאל, `.modal-wide`, ומחלקות `.drive-*` של הבורר · שורות 1013–1189

```css
/* ─── Modal ─────────────────────────────────────────────────── */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: var(--color-bg-modal);
  z-index: var(--z-modal-backdrop);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-xl);
  opacity: 0;
  visibility: hidden;
  transition: all var(--transition-normal);
}

.modal-backdrop.active {
  opacity: 1;
  visibility: visible;
}

.modal {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  width: 100%;
  max-width: 560px;
  max-height: 85vh;
  overflow-y: auto;
  transform: translateY(20px) scale(0.97);
  transition: transform var(--transition-normal);
}

.modal-backdrop.active .modal {
  transform: translateY(0) scale(1);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg) var(--space-xl);
  border-block-end: 1px solid var(--color-border);
}

.modal-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
}

.modal-close {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  border: none;
  background: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 18px;
  transition: all var(--transition-fast);
}

.modal-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.modal-body {
  padding: var(--space-xl);
  display: flex;
  flex-direction: column;
  gap: var(--space-base);
}

.modal-footer {
  display: flex;
  justify-content: flex-start;
  gap: var(--space-sm);
  padding: var(--space-base) var(--space-xl);
  border-block-start: 1px solid var(--color-border);
}

/* Wide modal for Drive browser */
.modal-wide {
  max-width: 720px;
}

/* ─── Drive File Browser ────────────────────────────────────── */
.drive-browser {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-md);
  max-height: 400px;
  overflow-y: auto;
  padding: var(--space-xs);
}

.drive-file {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-md);
  border: 2px solid transparent;
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all var(--transition-fast);
  text-align: center;
}

.drive-file:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-border);
}

.drive-file.selected {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

.drive-file-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  overflow: hidden;
}

.drive-file-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.drive-file-name {
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.drive-folder {
  cursor: pointer;
}

.drive-breadcrumb {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  margin-block-end: var(--space-md);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  flex-wrap: wrap;
}

.drive-breadcrumb-item {
  cursor: pointer;
  color: var(--color-accent);
  transition: opacity var(--transition-fast);
}

.drive-breadcrumb-item:hover {
  opacity: 0.8;
}

.drive-breadcrumb-separator {
  color: var(--color-text-muted);
}
```

**`static/style.css`** · הודעות (toast), מצב ריק (`.empty-state`), ספינר (`.spinner`, ‏`.loading-overlay`) · שורות 1424–1499

```css
/* ─── Toast Notifications ───────────────────────────────────── */
.toast-container {
  position: fixed;
  top: var(--space-xl);
  inset-inline-start: var(--space-xl);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.toast {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-md) var(--space-lg);
  display: flex;
  align-items: center;
  gap: var(--space-md);
  min-width: 280px;
  max-width: 400px;
  animation: slideIn 200ms ease forwards;
  font-size: var(--font-size-sm);
}

.toast.toast-success { border-inline-start: 3px solid var(--color-success); }
.toast.toast-error { border-inline-start: 3px solid var(--color-error); }
.toast.toast-info { border-inline-start: 3px solid var(--color-info); }

.toast.removing {
  animation: slideOut 200ms ease forwards;
}

/* ─── Empty State ───────────────────────────────────────────── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-4xl) var(--space-xl);
  text-align: center;
  color: var(--color-text-muted);
}

.empty-state-icon {
  font-size: 48px;
  margin-block-end: var(--space-base);
  opacity: 0.5;
}

.empty-state-text {
  font-size: var(--font-size-md);
  margin-block-end: var(--space-sm);
}

.empty-state-sub {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

/* ─── Loading Spinner ───────────────────────────────────────── */
.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.loading-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
}
```

**`static/style.css`** · אנימציות (`spin`, ‏`slideIn`, ‏`slideOut`, ‏`pulse`) · שורות 1566–1596

```css
/* ─── Animations ────────────────────────────────────────────── */
@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes slideOut {
  from {
    opacity: 1;
    transform: translateX(0);
  }
  to {
    opacity: 0;
    transform: translateX(-20px);
  }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
```

**`static/style.css`** · מחלקות עזר (`.hidden`, ‏`.flex`, ‏`.gap-sm`, ‏`.mt-sm`...) ו-`.drive-file-id-display` · שורות 1637–1666

```css
/* ─── Utility Classes ───────────────────────────────────────── */
.hidden { display: none !important; }
.text-center { text-align: center; }
.text-start { text-align: start; }
.mt-sm { margin-block-start: var(--space-sm); }
.mt-md { margin-block-start: var(--space-md); }
.mt-lg { margin-block-start: var(--space-lg); }
.mb-sm { margin-block-end: var(--space-sm); }
.mb-md { margin-block-end: var(--space-md); }
.gap-sm { gap: var(--space-sm); }
.flex { display: flex; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }

/* file id display in form */
.drive-file-id-display {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.drive-file-id-display .file-name {
  color: var(--color-text-primary);
  font-weight: 500;
}
```

### 10א. התאמות מובייל

כל השורות בקטע הזה נמצאות בתוך `@media (max-width: 640px)` אחד, שנפתח בשורה 1709 ונסגר בשורה 1857. השורות שביניהם ולא הועתקו שייכות לחלקים אחרים בפאנל.

**`static/style.css`** · פתיחת ה-media query · שורות 1708–1709

```css
/* Mobile */
@media (max-width: 640px) {
```

**`static/style.css`** · `.modal`, ‏`.modal-wide`, ‏`.drive-browser` במובייל · שורות 1753–1765

```css
  .modal {
    max-width: 100%;
    margin: var(--space-sm);
    max-height: 95vh;
  }

  .modal-wide {
    max-width: 100%;
  }

  .drive-browser {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  }
```

**`static/style.css`** · `#drive-modal` במסך מלא במובייל · שורות 1830–1848

```css
  /* Post form modal full-screen on mobile; small dialogs stay centered */
  #post-modal,
  #drive-modal {
    padding: 0;
  }

  #post-modal .modal,
  #drive-modal .modal {
    max-width: 100%;
    max-height: 100%;
    height: 100%;
    border-radius: 0;
    margin: 0;
  }

  #post-modal .modal-body,
  #drive-modal .modal-body {
    padding: var(--space-base);
  }
```

**`static/style.css`** · סגירת ה-media query · שורה 1857

```css
}
```

**`static/style.css`** · Reduced Motion · שורות 2005–2013

> מבטל אנימציות ומעברים, כולל של המודאל וההודעות.

```css
/* ─── Reduced Motion ────────────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

## 11. `Dockerfile.web` ו-`requirements.txt`

**`Dockerfile.web`** · `Dockerfile.web` — הקובץ המלא · שורות 1–24

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Lower glibc's trim threshold so memory freed back to malloc is actually
# returned to the OS. Without this, RSS stays high after a spike (e.g.
# Pillow buffers, googleapiclient response bodies) and Render's 512MB
# limit eventually trips even though the heap is mostly unused.
ENV MALLOC_TRIM_THRESHOLD_=131072

# One worker with threads (not two sync workers): the app is I/O-bound on
# Google APIs, Google clients are already thread-safe via threading.local,
# and on Render's 512MB starter plan halving the baseline process count is
# the biggest single lever for avoiding OOM during burst traffic (e.g. when
# a user deletes many posts one after another).
#
# --max-requests is intentionally low: googleapiclient and Pillow buffers
# fragment the heap over time, so recycling the worker every ~250 requests
# returns memory to the OS before it can drift past Render's 512MB limit.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--max-requests", "250", "--max-requests-jitter", "50", "web_app:app"]
```

**`requirements.txt`** · `requirements.txt` — הקובץ המלא · שורות 1–8

```text
google-api-python-client==2.147.0
google-auth==2.35.0
cloudinary==1.41.0
requests==2.32.3
python-dateutil==2.9.0
Pillow==11.1.0
flask==3.1.0
gunicorn==23.0.0
```

## 12. משתני סביבה

| משתנה | איפה נקרא | חובה? | מה קורה כשהוא חסר |
|---|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `config.py` שורה 15 | כן | `KeyError` בייבוא `config` |
| `SPREADSHEET_ID` | `config.py` שורה 16 | כן, גם בלי שימוש בגיליון | `KeyError` בייבוא `config` |
| `SHEET_NAME` | `config.py` שורה 17 | לא | ברירת מחדל `Sheet1` |
| `CLOUDINARY_URL` | `config.py` שורה 39 | כן, או שלושת הבאים | `RuntimeError` בייבוא `config` |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | `config.py` שורות 34–36 | כן, אם אין `CLOUDINARY_URL` | `RuntimeError` בייבוא `config` |
| `GOOGLE_DRIVE_FOLDER_ID` | `web_app.py` שורה 263 | כן, לבורר | `/api/drive/files` מחזיר 400, התמונה הממוזערת מחזירה 404, והפרונט מציג הודעה שלא הוגדרה תיקייה |
| `WEB_PANEL_SECRET` | `web_app.py` שורה 105 | מומלץ מאוד | אין אימות בכלל (אזהרה בלוג) |
| `WEB_PANEL_DEV_SECRET` | `web_app.py` שורה 106 | לא | אין כניסת מפתח |
| `FLASK_DEBUG` | `web_app.py` שורה 1080 | לא | מצב הדיבאג של `api_drive_thumbnail` כבוי |
| `GBP_OAUTH_CLIENT_ID`, `GBP_OAUTH_CLIENT_SECRET` | `config.py` שורות 56–57, `scripts/google_oauth.py` שורות 29–30 | רק ל-OAuth של GBP | הסקריפט יוצא עם שגיאה; `GoogleOAuthManager` זורק `ValueError` |
| `GBP_REFRESH_TOKEN` | `config.py` שורה 58 | רק ל-OAuth של GBP | `GoogleOAuthManager` זורק `ValueError` |
| `GBP_ACCOUNT_ID`, `GBP_DEFAULT_LOCATION_ID` | `config.py` שורות 54–55 | לא | מופיעים בקטע שהועתק, אבל הקוד כאן לא משתמש בהם |
| `MALLOC_TRIM_THRESHOLD_` | `Dockerfile.web` (`ENV`) | — | מוגדר בתוך ה-Dockerfile עצמו, לא צריך להגדיר בנפרד |
