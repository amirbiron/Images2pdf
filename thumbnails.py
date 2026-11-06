"""
thumbnails.py — /api/drive/thumbnail/<file_id>: תמונה ממוזערת דרך השרת.

התיעוד של גוגל על `thumbnailLink`: "Not intended for direct usage on web
applications due to CORS policies. Consider using a proxy server." וגם: "If the
file isn't shared publicly, the URL ... must be fetched using a credentialed
request." מקור: https://developers.google.com/workspace/drive/api/reference/rest/v3/files
בדיקה 5 ב-/dev/checks קובעת אם צריך את הנתיב הזה בפועל (ROADMAP, טבלת השינויים).

ההבדלים מ-`api_drive_thumbnail` המקורי: אין בדיקת תיקיית שורש ואין מסלול גיבוי
שקורא גיליון; הטוקן נלקח מה-Credentials שלנו ולא מ-`svc._http` (שדה פרטי);
והטוקן נשלח רק למארח שעבר בדיקה מבנית.
"""

from __future__ import annotations

import logging
import re

import google.auth.transport.requests as google_requests
import requests
from flask import Blueprint, Response, request
from googleapiclient.errors import HttpError

from config import is_valid_drive_id
from google_api import drive_get, get_credentials, http_status, is_allowed_thumbnail_url, thumbnail_url_host

logger = logging.getLogger(__name__)

bp = Blueprint("thumbnails", __name__)

MAX_THUMB_BYTES = 5 * 1024 * 1024
# התצוגה המוגדלת בבורר. הקישור של גוגל מסתיים ב-`=s220`, והפאנל המקורי החליף אותו
# בגודל גדול יותר לתצוגה המוגדלת (docs/drive-picker-extract.md, סעיף 6, api_drive_thumbnail:
# "Replace with a larger size when requested for the lightbox"). התיעוד של Drive לא מתאר
# את הסיומת הזו, ולכן קישור שלא מסתיים בה נשאר כמו שהוא.
LARGE_SIZE = 1600
_SIZE_SUFFIX = re.compile(r"=s\d+$")


def sized_link(link: str, size: int) -> str:
    return _SIZE_SUFFIX.sub(f"=s{size}", link) if _SIZE_SUFFIX.search(link) else link


@bp.get("/api/drive/thumbnail/<file_id>")
def thumbnail(file_id: str):
    if not is_valid_drive_id(file_id):
        return Response(status=400)
    size = request.args.get("size")
    if size not in (None, "large"):
        return Response(status=400)
    try:
        meta = drive_get(file_id, "thumbnailLink")
    except HttpError as exc:
        status = http_status(exc)
        logger.info("Thumbnail metadata failed: HTTP %s", status)
        return Response(status=404 if status in (403, 404) else 502)

    link = meta.get("thumbnailLink")
    if link is None:
        return Response(status=404)
    if size == "large" and isinstance(link, str):
        link = sized_link(link, LARGE_SIZE)
    if not is_allowed_thumbnail_url(link):
        logger.warning("thumbnailLink host not allowed for credentialed fetch: %s", thumbnail_url_host(link))
        return Response(status=502)

    headers: dict[str, str] = {}
    # before_request מרענן את הטוקן אם צריך ומוסיף Authorization — אותו מנגנון
    # ש-AuthorizedSession משתמש בו. RefreshError עולה ל-error handler של האפליקציה.
    # מקור: google/auth/credentials.py Credentials.before_request
    get_credentials().before_request(google_requests.Request(), "GET", link, headers)

    try:
        resp = requests.get(link, headers=headers, timeout=10, stream=True)
    except requests.RequestException as exc:
        logger.warning("Thumbnail fetch failed: %s", type(exc).__name__)
        return Response(status=502)
    try:
        if resp.status_code != 200:
            logger.warning("Thumbnail fetch returned HTTP %s", resp.status_code)
            return Response(status=502)
        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            # רק תמונות: תוכן אחר מאותו מקור היה יכול לרוץ אצלנו כ-HTML.
            logger.warning("Thumbnail fetch returned a non-image content type")
            return Response(status=502)
        data = resp.raw.read(MAX_THUMB_BYTES + 1, decode_content=True)
    finally:
        resp.close()
    if len(data) > MAX_THUMB_BYTES:
        return Response(status=413)
    return Response(
        data,
        mimetype=content_type.split(";")[0].strip(),
        # הכתובת קבועה לכל קובץ, ולכן מטמון קצר בלבד ופרטי (התוכן מאחורי סיסמה).
        headers={"Cache-Control": "private, max-age=600"},
    )
