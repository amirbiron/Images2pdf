"""
picker_api.py — ה-API של הבורר (שלב 2): חיפוש לוקיישן, רשימת התוכן של תיקייה, ושם של תיקייה.

כל התשובות JSON. כשל הרשאה (RefreshError, חשבון לא מחובר) מטופל ב-web_app ומחזיר
`{"error": "google_reconnect_required"}`, כדי שהדפדפן יציג מסך חיבור מחדש.
שמות קבצים ותיקיות חוזרים כמו שהם, והדפדפן מכניס אותם לדף עם textContent בלבד.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from googleapiclient.errors import HttpError

from config import is_valid_drive_id
from google_api import (
    FOLDER_MIME,
    drive_folder_names,
    drive_get,
    drive_list_folder,
    drive_search_folders,
    http_error_reason,
    http_status,
    is_allowed_thumbnail_url,
)

logger = logging.getLogger(__name__)

bp = Blueprint("picker_api", __name__)

# שם לוקיישן ארוך במיוחד הוא בערך 60 תווים; 100 משאיר שוליים. ארוך מזה — נדחה, לא נחתך.
QUERY_MAX_CHARS = 100
# טוקן עמוד של דרייב הוא מחרוזת אטומה; בפועל כמה מאות תווים.
PAGE_TOKEN_MAX_CHARS = 2000
SEARCH_PAGE_SIZE = 30
LIST_PAGE_SIZE = 100


def _error(code: str, status: int):
    return jsonify({"error": code}), status


def _page_token() -> tuple[str | None, bool]:
    """(טוקן, תקין?). טוקן עובר לדרייב כפרמטר ולא נבנה לתוך שאילתה, אבל עדיין מוגבל."""
    token = request.args.get("page_token")
    if token is None or token == "":
        return None, True
    if len(token) > PAGE_TOKEN_MAX_CHARS or not token.isprintable() or not token.isascii():
        return None, False
    return token, True


def _drive_failure(exc: HttpError, what: str):
    status = http_status(exc)
    logger.warning("Drive %s failed: HTTP %s %s", what, status, http_error_reason(exc))
    if status == 404:
        return _error("not_found", 404)
    if status == 403:
        return _error("forbidden", 403)
    return _error("drive_error", 502)


@bp.get("/api/drive/search")
def search():
    """חיפוש תיקיות לפי תחילת מילה בשם. כל תוצאה כוללת את שם תיקיית האב,
    כי שמות כמו "חדרי אמבטיה" חוזרים בהרבה לוקיישנים (ROADMAP)."""
    q = request.args.get("q")
    if q is None or q.strip() == "":
        return _error("query_missing", 400)
    q = " ".join(q.split())
    if len(q) > QUERY_MAX_CHARS:
        return _error("query_too_long", 400)
    token, ok = _page_token()
    if not ok:
        return _error("bad_page_token", 400)
    try:
        page = drive_search_folders(q, token, page_size=SEARCH_PAGE_SIZE)
        found = [f for f in page["files"] if is_valid_drive_id(f.get("id")) and isinstance(f.get("name"), str)]
        parent_ids = []
        for f in found:
            parents = f.get("parents")
            if isinstance(parents, list) and parents and is_valid_drive_id(parents[0]):
                parent_ids.append(parents[0])
        names = drive_folder_names(parent_ids)
    except HttpError as exc:
        return _drive_failure(exc, "search")

    results = []
    for f in found:
        parents = f.get("parents")
        parent_id = parents[0] if isinstance(parents, list) and parents else None
        parent_name = names.get(parent_id) if parent_id else None
        results.append({
            "id": f["id"],
            "name": f["name"],
            "parent": {"id": parent_id, "name": parent_name} if parent_name else None,
        })
    return jsonify({
        "results": results,
        "nextPageToken": page["nextPageToken"],
        "incomplete": page["incompleteSearch"],
    })


@bp.get("/api/drive/folder/<folder_id>")
def folder(folder_id: str):
    """שם התיקייה — לטעינה מחדש של הדף בתוך לוקיישן, כשהשם לא שמור בדפדפן."""
    if not is_valid_drive_id(folder_id):
        return _error("bad_folder_id", 400)
    try:
        meta = drive_get(folder_id, "id, name, mimeType, trashed")
    except HttpError as exc:
        return _drive_failure(exc, "folder lookup")
    if meta.get("mimeType") != FOLDER_MIME or meta.get("trashed") is True or not isinstance(meta.get("name"), str):
        return _error("not_found", 404)
    return jsonify({"id": folder_id, "name": meta["name"]})


@bp.get("/api/drive/files")
def files():
    """עמוד אחד מהתוכן של תיקייה: תתי-תיקיות ותמונות JPEG/PNG בלבד.

    `folder_id` חובה: אין תיקיית שורש (ROADMAP). הדפדפן מבקש את העמוד הבא לפי
    nextPageToken עד הסוף, כדי שתיקייה עם יותר מ-50 תמונות תוצג כולה.
    """
    folder_id = request.args.get("folder_id")
    if not is_valid_drive_id(folder_id):
        return _error("bad_folder_id", 400)
    token, ok = _page_token()
    if not ok:
        return _error("bad_page_token", 400)
    try:
        page = drive_list_folder(folder_id, token, page_size=LIST_PAGE_SIZE)
    except HttpError as exc:
        return _drive_failure(exc, "folder listing")

    folders, images = [], []
    for f in page["files"]:
        fid, name = f.get("id"), f.get("name")
        if not is_valid_drive_id(fid) or not isinstance(name, str):
            continue
        if f.get("mimeType") == FOLDER_MIME:
            folders.append({"id": fid, "name": name})
        else:
            link = f.get("thumbnailLink")
            # רק קישור למארח של גוגל נכנס ל-src. אחרת הדפדפן הולך ישר לנתיב שדרך השרת.
            images.append({"id": fid, "name": name, "thumbnailLink": link if is_allowed_thumbnail_url(link) else None})
    return jsonify({"folders": folders, "images": images, "nextPageToken": page["nextPageToken"]})
