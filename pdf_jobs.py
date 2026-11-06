"""
pdf_jobs.py — יצירת PDF מהבורר (שלב 3): בקשת יצירה, מצב העבודה, והורדת הקובץ.

החוזה מול הדפדפן:
- POST /api/pdf/jobs עם מזהה שהדפדפן יצר. היצירה רצה בתוך הבקשה הזו (ROADMAP: "בקשה
  אחת שממתינים לה, בלי עבודה ברקע ובלי תור"), והתשובה היא מצב העבודה בסיום.
- אם החיבור של הדפדפן נופל באמצע (מסך נעול, מעבר לאפליקציה אחרת), הבקשה ממשיכה לרוץ
  בשרת עד הסוף, כולל השמירה בדרייב: gunicorn לא עוצר את ה-view כשהלקוח מתנתק, ומגלה
  את הניתוק רק כשהוא כותב את התשובה. נמדד ב-tests/test_pdf_jobs_gunicorn.py.
- כשהדפדפן חוזר הוא שואל GET /api/pdf/jobs/<id>. אותו מזהה ב-POST נוסף (לחיצה כפולה,
  ניסיון חוזר אחרי ניתוק) מחזיר את העבודה הקיימת ולא יוצר קובץ שני.
- המצב נשמר בקובץ JSON בדיסק הקבוע. לא בזיכרון, כי מחזור worker (max_requests) מעביר
  את הבקשות הבאות לתהליך אחר. ולא ב-session, כי עוגיית session נדרסת בבקשות מקבילות
  (CLAUDE.md, מדיניות דפדפן 9).
- הקבצים נשמרים 24 שעות ואז נמחקים. זה לא מסך היסטוריה (ROADMAP: "לא בגרסה הראשונה"),
  אלא הזמן שבו אפשר לחזור לקובץ אחרי ניתוק.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httplib2
from flask import Blueprint, current_app, jsonify, request, send_file
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from config import is_valid_drive_id
from google_api import (
    FOLDER_MIME,
    IMAGE_MIMES,
    MAX_DOWNLOAD_BYTES,
    DownloadTooLarge,
    GoogleNotConnected,
    WrongFileType,
    drive_create_folder,
    drive_download_to_file,
    drive_get,
    drive_upload_file,
    http_error_reason,
    http_status,
)
from pdf_builder import TITLE_MAX_CHARS, clean_title, drawable_title, folder_url, pdf_filename
from pdf_job import MAX_IMAGES, FetchError, GenerationLock, SourceItem, run_pdf_job
from storage import atomic_write_bytes, data_dir_problem, file_lock, remove_stale_entries

logger = logging.getLogger(__name__)

bp = Blueprint("pdf_jobs", __name__)

# המזהה נוצר בדפדפן (16 בתים אקראיים, hex). הוא נכנס לנתיב של קובץ, ולכן רק התבנית הזו.
JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
# שמות התמונות מגיעים מהדפדפן ומוצגים רק בהודעת כשל (textContent). שם ארוך במיוחד
# הוא כמה עשרות תווים; 300 משאיר שוליים. ארוך מזה — נדחה, לא נחתך.
IMAGE_NAME_MAX_CHARS = 300
KEEP_SECONDS = 24 * 3600
MAX_KEPT_JOBS = 30
STALE_TMP_SECONDS = 3600
PDF_FOLDER_NAME = "קבצי PDF"


@dataclass(frozen=True)
class JobRequest:
    id: str
    location_id: str
    title: str
    items: tuple[SourceItem, ...]


# ─── מקום בדיסק ───────────────────────────────────────────────

def _data_dir() -> Path:
    return current_app.extensions["images2pdf"].settings.data_dir


def _jobs_dir() -> Path:
    return _data_dir() / "jobs"


def _record_path(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job id")
    return _jobs_dir() / f"{job_id}.json"


def _pdf_path(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job id")
    return _jobs_dir() / f"{job_id}.pdf"


def _load(job_id: str) -> dict | None:
    try:
        data = json.loads(_record_path(job_id).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read a PDF job record")
        return None
    return data if isinstance(data, dict) and data.get("id") == job_id else None


def _save(record: dict) -> None:
    record["updated_at"] = time.time()
    atomic_write_bytes(_record_path(record["id"]), json.dumps(record, ensure_ascii=False).encode("utf-8"))


def _remove(job_id: str) -> None:
    for path in (_pdf_path(job_id), _record_path(job_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove an old PDF job file", exc_info=True)


def _cleanup_old_jobs() -> None:
    """עבודות שהסתיימו לפני יותר מ-24 שעות, וכל מה שמעבר ל-MAX_KEPT_JOBS האחרונות.

    נקרא רק אחרי שהבקשה הנוכחית קיבלה את נעילת היצירה, ולפני שהרשומה שלה נשמרה. לכן
    באותו רגע אף עבודה אחרת לא רצה, וכל רשומה אחרת שמסומנת "רצה" נקטעה (תהליך שנהרג
    באמצע). בלי זה, רשומה כזו שאף דפדפן לא שואל עליה נשארת בדיסק לתמיד. השמירה שלה
    נמדדת מהזמן שבו היא התקדמה לאחרונה, ולא מהרגע שבו סומנה כנקטעת.
    """
    records = []
    for path in _jobs_dir().glob("*.json"):
        job_id = path.stem
        if not JOB_ID_RE.fullmatch(job_id):
            continue
        record = _load(job_id)
        if record is None:
            continue
        last_update = record.get("updated_at") or 0
        if record.get("status") == "running":
            logger.warning("Found an interrupted PDF job during cleanup")
            record.update(status="failed", error="interrupted")
            _save(record)
        records.append((last_update, job_id))
    records.sort(reverse=True)
    now = time.time()
    for i, (updated, job_id) in enumerate(records):
        if i >= MAX_KEPT_JOBS or now - updated > KEEP_SECONDS:
            _remove(job_id)


# ─── הבקשה מהדפדפן ────────────────────────────────────────────

def _parse(body: object) -> tuple[JobRequest | None, str | None]:
    """(בקשה, None) או (None, קוד שגיאה). כל שדה נבדק בסוג ובאורך לפני שימוש."""
    if not isinstance(body, dict):
        return None, "bad_request"
    job_id = body.get("id")
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        return None, "bad_job_id"
    location_id = body.get("location_id")
    if not is_valid_drive_id(location_id):
        return None, "bad_folder_id"
    raw_title = body.get("title")
    if not isinstance(raw_title, str) or len(raw_title) > TITLE_MAX_CHARS * 2:
        return None, "bad_title"
    try:
        title = clean_title(raw_title)
    except ValueError:
        return None, "bad_title"
    if not drawable_title(title):
        return None, "title_not_drawable"
    images = body.get("images")
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
        return None, "bad_images"
    items = []
    for image in images:
        if not isinstance(image, dict):
            return None, "bad_images"
        image_id, name = image.get("id"), image.get("name")
        if not is_valid_drive_id(image_id) or not isinstance(name, str) or not 0 < len(name) <= IMAGE_NAME_MAX_CHARS:
            return None, "bad_images"
        items.append(SourceItem(id=image_id, name=name))
    if len({i.id for i in items}) != len(items):
        return None, "bad_images"
    return JobRequest(id=job_id, location_id=location_id, title=title, items=tuple(items)), None


def _error(code: str, status: int):
    return jsonify({"error": code}), status


# ─── מה הדפדפן רואה ───────────────────────────────────────────

def _drive_link(url: object) -> str | None:
    """רק קישור של דרייב עובר לדפדפן: https, המארח drive.google.com בדיוק, פורט רגיל."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname != "drive.google.com" or port not in (None, 443):
        return None
    if parts.username or parts.password:
        return None
    return url


def _public(record: dict) -> dict:
    done = record.get("status") == "done"
    return {
        "id": record["id"],
        "status": record.get("status"),
        "stage": record.get("stage"),
        "progress": {"done": record.get("done", 0), "total": record.get("total", 0)},
        "title": record.get("title"),
        "filename": record.get("filename"),
        "pages": record.get("pages", 0),
        "file_url": f"/api/pdf/jobs/{record['id']}/file" if done and record.get("pdf") else None,
        "drive_url": _drive_link(record.get("drive_url")),
        "saved_to_drive": bool(record.get("drive_file_id")),
        "drive_error": record.get("drive_error"),
        "failed": record.get("failed", []),
        "error": record.get("error"),
    }


def _respond(record: dict):
    return jsonify(_public(record)), 202 if record.get("status") == "running" else 200


def _mark_orphan_if_dead(record: dict, lock: GenerationLock) -> dict:
    """עבודה שרשומה כרצה, כשאף אחד לא מחזיק את נעילת היצירה, נקטעה (פריסה חדשה או
    תהליך שנהרג). נקרא רק בתוך נעילת הרישום, כמו פתיחת עבודה חדשה, ולכן אין מרוץ:
    עבודה חיה מחזיקה את נעילת היצירה מהרגע שנרשמה ועד אחרי הרישום האחרון שלה."""
    if record.get("status") == "running" and lock.is_free():
        logger.warning("PDF job was interrupted (no process holds the generation lock)")
        record.update(status="failed", error="interrupted")
        _save(record)
    return record


# ─── דרייב ────────────────────────────────────────────────────

def drive_fetch(item: SourceItem, dest) -> int:
    """הורדת תמונה מדרייב. כשל של קובץ בודד הופך ל-FetchError עם סיבה בעברית;
    RefreshError ו-GoogleNotConnected לא נתפסים כאן ועולים למסך החיבור מחדש."""
    try:
        return drive_download_to_file(item.id, dest, require_mimes=IMAGE_MIMES)
    except WrongFileType:
        raise FetchError("זה לא קובץ JPEG או PNG.") from None
    except DownloadTooLarge:
        raise FetchError(f"הקובץ גדול מ-{MAX_DOWNLOAD_BYTES // (1024 * 1024)}MB.") from None
    except HttpError as exc:
        status = http_status(exc)
        logger.warning("Drive download failed: HTTP %s %s", status, http_error_reason(exc))
        if status == 404:
            raise FetchError("התמונה לא נמצאה בדרייב (אולי נמחקה או הועברה).") from None
        raise FetchError(f"ההורדה מדרייב נכשלה (HTTP {status}).") from None
    except (httplib2.HttpLib2Error, socket.timeout, ConnectionError) as exc:
        logger.warning("Drive download failed: %s", type(exc).__name__)
        raise FetchError("ההורדה מדרייב נכשלה (תקלת רשת).") from None


def _pdf_folder_id() -> str:
    """התיקייה `קבצי PDF` (ROADMAP, "שמירת הקובץ"): נוצרת בפעם הראשונה, והמזהה נשמר בדיסק.

    אם התיקייה נמחקה או הועברה לאשפה — נוצרת חדשה. נקרא רק מתוך עבודה שמחזיקה את נעילת
    היצירה, ולכן שתי עבודות לא יוצרות שתי תיקיות.
    """
    path = _data_dir() / "pdf_folder.json"
    stored = None
    try:
        data = json.loads(path.read_bytes())
        stored = data.get("id") if isinstance(data, dict) else None
    except FileNotFoundError:
        stored = None  # הפעם הראשונה: עוד לא נוצרה תיקייה, ונוצרת עכשיו
    except (OSError, ValueError):
        logger.warning("Could not read the saved PDF folder id; a new folder will be created")
    if is_valid_drive_id(stored):
        try:
            meta = drive_get(stored, "id, mimeType, trashed")
        except HttpError as exc:
            if http_status(exc) != 404:
                raise
            meta = {}
        if meta.get("mimeType") == FOLDER_MIME and meta.get("trashed") is not True:
            return stored
        logger.info("The saved PDF folder is gone; creating a new one")
    created = drive_create_folder(PDF_FOLDER_NAME)
    folder_id = created.get("id") if isinstance(created, dict) else None
    if not is_valid_drive_id(folder_id):
        raise RuntimeError("Drive returned no folder id")
    try:
        atomic_write_bytes(path, json.dumps({"id": folder_id}).encode())
    except OSError:
        # הקובץ נשמר בתיקייה החדשה גם כך; בפעם הבאה תיווצר תיקייה נוספת באותו שם.
        logger.error("Could not save the PDF folder id", exc_info=True)
    return folder_id


# ─── העבודה ───────────────────────────────────────────────────

def _run(record: dict, spec: JobRequest) -> dict:
    """מריץ עבודה שכבר נרשמה, ומעדכן את הרישום בכל שלב. הרישום הוא מקור האמת לדפדפן."""

    def update(**changes) -> None:
        record.update(changes)
        _save(record)

    try:
        try:
            folder = drive_get(spec.location_id, "id, name, mimeType, trashed")
        except HttpError as exc:
            if http_status(exc) != 404:
                logger.warning("Location lookup failed: HTTP %s %s", http_status(exc), http_error_reason(exc))
                update(status="failed", error="drive_error")
                return record
            folder = {}
        if folder.get("mimeType") != FOLDER_MIME or folder.get("trashed") is True:
            update(status="failed", error="location_not_found")
            return record

        tmp_root = _data_dir() / "tmp"
        remove_stale_entries(tmp_root, STALE_TMP_SECONDS)
        tmp_root.mkdir(parents=True, exist_ok=True)
        pdf_path = _pdf_path(spec.id)
        with tempfile.TemporaryDirectory(dir=tmp_root, prefix="job-") as work:
            job = run_pdf_job(
                spec.items,
                drive_fetch,
                title=spec.title,
                link_url=folder_url(spec.location_id),
                out_path=pdf_path,
                work_dir=Path(work),
                on_progress=lambda done, total: update(done=done, total=total),
            )
        failed = [{"name": n, "reason": r} for n, r in job.failed]
        logger.info(
            "PDF job: ok=%s images=%d/%d pages=%s pdf_kb=%s seconds=%.1f rss_peak_mb=%s",
            job.ok, job.processed, len(spec.items), job.build.pages if job.build else 0,
            round(job.pdf_bytes / 1024), job.seconds_total,
            round(job.memory.rss_peak / 1048576) if job.memory and job.memory.rss_peak else None,
        )
        if not job.ok:
            update(status="failed", error="no_images", failed=failed)
            return record
        update(stage="saving", pdf=True, pages=job.build.pages, failed=failed)

        # השמירה בדרייב: כישלון לא מונע הורדה, אבל מוצג (ROADMAP, "שמירת הקובץ").
        try:
            uploaded = drive_upload_file(
                pdf_path, record["filename"], "application/pdf", _pdf_folder_id(), on_chunk=lambda: update()
            )
        except (RefreshError, GoogleNotConnected):
            update(status="done", stage=None, drive_error="google_reconnect_required")
            raise
        except (HttpError, httplib2.HttpLib2Error, OSError, RuntimeError) as exc:
            detail = f"HTTP {http_status(exc)} {http_error_reason(exc)}" if isinstance(exc, HttpError) else type(exc).__name__
            logger.warning("Saving the PDF to Drive failed: %s", detail)
            update(status="done", stage=None, drive_error="save_failed")
            return record
        file_id = uploaded.get("id")
        update(
            status="done",
            stage=None,
            drive_file_id=file_id if is_valid_drive_id(file_id) else None,
            drive_url=uploaded.get("webViewLink"),
            drive_error=None if is_valid_drive_id(file_id) else "save_failed",
        )
        return record
    except (RefreshError, GoogleNotConnected):
        if record.get("status") == "running":
            update(status="failed", error="google_reconnect_required")
        raise  # ה-error handler של האפליקציה מטפל בהרשאה (מוחק הרשאה מתה) ועונה לדפדפן
    except Exception:
        if record.get("status") == "running":
            update(status="failed", error="server_error")
        raise


# ─── נקודות הקצה ──────────────────────────────────────────────

@bp.post("/api/pdf/jobs")
def create_job():
    """יוצר PDF מהבחירה. עונה כשהיצירה הסתיימה (200), או מיד עם המצב הקיים (202/200)
    אם עבודה עם אותו מזהה כבר נרשמה. 409 busy: עבודה אחרת רצה עכשיו."""
    if data_dir_problem(_data_dir()):
        return _error("storage_unavailable", 503)
    spec, problem = _parse(request.get_json(silent=True))
    if problem:
        return _error(problem, 400)

    jobs = _jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    lock = GenerationLock(_data_dir())
    with file_lock(jobs / ".registry.lock"):
        existing = _load(spec.id)
        if existing is not None:
            return _respond(_mark_orphan_if_dead(existing, lock))
        if not lock.try_acquire():
            return _error("busy", 409)
        try:
            _cleanup_old_jobs()
            record = {
                "id": spec.id,
                "status": "running",
                "stage": "images",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "location_id": spec.location_id,
                "title": spec.title,
                "filename": pdf_filename(spec.title),
                "done": 0,
                "total": len(spec.items),
                "pdf": False,
                "failed": [],
            }
            _save(record)
        except BaseException:
            lock.release()
            raise
    try:
        return _respond(_run(record, spec))
    finally:
        lock.release()


@bp.get("/api/pdf/jobs/<job_id>")
def job_status(job_id: str):
    if not JOB_ID_RE.fullmatch(job_id):
        return _error("bad_job_id", 400)
    jobs = _jobs_dir()
    if not jobs.is_dir():
        return _error("not_found", 404)
    with file_lock(jobs / ".registry.lock"):
        record = _load(job_id)
        if record is None:
            return _error("not_found", 404)
        record = _mark_orphan_if_dead(record, GenerationLock(_data_dir()))
    return _respond(record)


@bp.get("/api/pdf/jobs/<job_id>/file")
def job_file(job_id: str):
    """הקובץ להורדה, בשם `<כותרת> <DD-MM-YYYY>.pdf`."""
    if not JOB_ID_RE.fullmatch(job_id):
        return _error("bad_job_id", 400)
    record = _load(job_id)
    path = _pdf_path(job_id)
    if record is None or record.get("status") != "done" or not record.get("pdf") or not path.is_file():
        return _error("not_found", 404)
    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=record.get("filename") or "קובץ.pdf",
        max_age=0,
    )
