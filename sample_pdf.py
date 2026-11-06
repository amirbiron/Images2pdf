"""
sample_pdf.py — הדף /dev/sample-pdf (שלב 1 ב-ROADMAP). יוסר בשלב 4.

יוצר PDF מ-12 התמונות הראשונות בתיקייה נתונה, ומציג את מה שצריך כדי להחליט על
המשך הדרך: זמן (הורדה, עיבוד, כתיבה), שיא זיכרון, ומשקל הקובץ. אותם מספרים נרשמים
בלוג, בלי שמות קבצים.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, redirect, render_template, request, send_file
from googleapiclient.errors import HttpError

from config import is_valid_drive_id
from google_api import FOLDER_MIME, drive_get, drive_list_folder, http_status
from pdf_builder import TITLE_MAX_CHARS, clean_title, drawable_title, folder_url, pdf_filename
from pdf_job import MAX_IMAGES, GenerationLock, SourceItem, run_pdf_job
from pdf_jobs import drive_fetch
from storage import atomic_write_bytes, data_dir_problem, remove_stale_entries

logger = logging.getLogger(__name__)

bp = Blueprint("sample_pdf", __name__)

_STALE_TMP_SECONDS = 3600


def _data_dir():
    return current_app.extensions["images2pdf"].settings.data_dir


def _pdf_path():
    return _data_dir() / "dev_sample.pdf"


def _result_path():
    return _data_dir() / "dev_sample_last.json"


def _tmp_root():
    return _data_dir() / "tmp"


def _load_last() -> dict | None:
    try:
        data = json.loads(_result_path().read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read the last sample PDF result")
        return None
    return data if isinstance(data, dict) else None


def _mb(value: int | None) -> float | None:
    return None if value is None else round(value / (1024 * 1024), 1)


def _first_images(folder_id: str) -> list[SourceItem]:
    items: list[SourceItem] = []
    token = None
    while len(items) < MAX_IMAGES:
        page = drive_list_folder(folder_id, token)
        for f in page["files"]:
            if f.get("mimeType") != FOLDER_MIME and is_valid_drive_id(f.get("id")) and isinstance(f.get("name"), str):
                items.append(SourceItem(id=f["id"], name=f["name"]))
        token = page["nextPageToken"]
        if not token:
            break
    return items[:MAX_IMAGES]


@bp.get("/dev/sample-pdf")
def page():
    """הטופס, ותוצאות הריצה האחרונה אם יש."""
    services = current_app.extensions["images2pdf"]
    disk_problem = data_dir_problem(services.settings.data_dir)
    last = _load_last() if disk_problem is None else None
    # כשהדיסק לא תקין לא נוגעים בקובץ ההרשאה: stat על נתיב בתוך "תיקייה" שהיא קובץ
    # זורק NotADirectoryError, והדף שאמור להציג את הבעיה היה נופל ב-500.
    connected = (
        disk_problem is None and services.token_store is not None and services.token_store.version() is not None
    )
    return render_template(
        "sample_pdf.html",
        last=last,
        pdf_ready=last is not None and last.get("ok") and _pdf_path().is_file(),
        default_folder_id=(last or {}).get("folder_id") or services.settings.dev_sample_folder_id or "",
        disk_problem=disk_problem,
        google_problems=services.settings.google_problems,
        connected=connected,
        title_max=TITLE_MAX_CHARS,
    )


def _error(title: str, message: str, status: int, hint: str | None = None):
    return render_template("message.html", title=title, message=message, hint=hint), status


@bp.post("/dev/sample-pdf/run")
def run():
    """יוצר PDF מ-12 התמונות הראשונות בתיקייה, שומר אותו ואת המדידות, וחוזר לדף."""
    folder_id = (request.form.get("folder_id") or "").strip()
    if not is_valid_drive_id(folder_id):
        return _error("מזהה תיקייה לא תקין", "מזהה תיקייה מכיל רק אותיות באנגלית, ספרות, מקף וקו תחתון.", 400,
                      "מעתיקים את החלק שאחרי folders/ בכתובת של התיקייה בדרייב.")
    raw_title = request.form.get("title") or ""
    if len(raw_title) > TITLE_MAX_CHARS * 2:
        return _error("הכותרת ארוכה מדי", f"הכותרת יכולה להכיל עד {TITLE_MAX_CHARS} תווים.", 400)

    # אותה נעילה כמו בבורר: יצירה אחת בכל רגע, גם בין הדף הזה לבורר (זיכרון של 512MB).
    lock = GenerationLock(_data_dir())
    if not lock.try_acquire():
        return _error("כבר נוצר קובץ", "יצירת קובץ קודמת עדיין רצה. מחכים שתסתיים ומרעננים את הדף.", 409)
    try:
        try:
            folder = drive_get(folder_id, "id, name, mimeType")
        except HttpError as exc:
            if http_status(exc) == 404:
                return _error("התיקייה לא נמצאה", "החשבון שחובר לא רואה תיקייה עם המזהה הזה.", 404)
            raise
        if folder.get("mimeType") != FOLDER_MIME or not isinstance(folder.get("name"), str):
            return _error("זו לא תיקייה", "המזהה שהוזן שייך לקובץ ולא לתיקייה.", 400)
        try:
            title = clean_title(raw_title if raw_title.strip() else folder["name"])
        except ValueError:
            return _error("כותרת לא תקינה", f"הכותרת צריכה להכיל בין 1 ל-{TITLE_MAX_CHARS} תווים.", 400)
        if not drawable_title(title):
            return _error("כותרת לא תקינה", "בכותרת אין אף תו שאפשר להציג בקובץ (למשל רק אמוג'י).", 400,
                          "מקלידים כותרת אחרת בשדה הכותרת.")

        items = _first_images(folder_id)
        if not items:
            return _error("אין תמונות בתיקייה", "בתיקייה הזו אין תמונות JPEG או PNG (תתי-תיקיות לא נסרקות כאן).", 400)

        remove_stale_entries(_tmp_root(), _STALE_TMP_SECONDS)
        _tmp_root().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=_tmp_root(), prefix="sample-") as work:
            job = run_pdf_job(
                items,
                drive_fetch,
                title=title,
                link_url=folder_url(folder_id),
                out_path=_pdf_path(),
                work_dir=Path(work),
            )

        mem = job.memory
        summary = {
            "ok": job.ok,
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "folder_id": folder_id,
            "title": title,
            "filename": pdf_filename(title),
            "requested": len(items),
            "processed": job.processed,
            "failed": [{"name": n, "reason": r} for n, r in job.failed],
            "orientation": job.layout.orientation if job.layout else None,
            "pages": job.build.pages if job.build else 0,
            "removed_characters": job.build.removed_characters if job.build else "",
            "source_mb": _mb(job.source_bytes),
            "pdf_kb": round(job.pdf_bytes / 1024) if job.pdf_bytes else 0,
            "seconds": {
                "total": round(job.seconds_total, 1),
                "fetch": round(job.seconds_fetch, 1),
                "process": round(job.seconds_process, 1),
                "build": round(job.seconds_build, 1),
            },
            "memory_mb": {
                "rss_start": _mb(mem.rss_start) if mem else None,
                "rss_peak": _mb(mem.rss_peak) if mem else None,
                "cgroup_start": _mb(mem.cgroup_start) if mem else None,
                "cgroup_peak": _mb(mem.cgroup_peak) if mem else None,
                "samples": mem.samples if mem else 0,
            },
        }
        logger.info(
            "Sample PDF: ok=%s images=%d/%d failed=%d pages=%s orientation=%s pdf_kb=%s source_mb=%s "
            "seconds total=%s fetch=%s process=%s build=%s memory_mb rss_peak=%s cgroup_peak=%s",
            summary["ok"], summary["processed"], summary["requested"], len(summary["failed"]), summary["pages"],
            summary["orientation"], summary["pdf_kb"], summary["source_mb"],
            summary["seconds"]["total"], summary["seconds"]["fetch"], summary["seconds"]["process"],
            summary["seconds"]["build"], summary["memory_mb"]["rss_peak"], summary["memory_mb"]["cgroup_peak"],
        )
        try:
            atomic_write_bytes(_result_path(), json.dumps(summary, ensure_ascii=False).encode("utf-8"))
        except OSError:
            logger.error("Could not save the sample PDF result", exc_info=True)
            if job.ok:
                # ה-PDF כבר הוחלף בחדש, אבל התוצאה הישנה (עם שם הקובץ הישן) נשארה. מוחקים
                # אותה, כדי שהדף לא יציג את הריצה הקודמת ויוריד את הקובץ החדש תחת שמה.
                try:
                    _result_path().unlink(missing_ok=True)
                except OSError:
                    logger.error("Could not remove the stale sample PDF result", exc_info=True)
            return _error("התוצאה לא נשמרה", "הקובץ נוצר, אבל שמירת התוצאה לדיסק נכשלה.", 500)
    finally:
        lock.release()
    return redirect("/dev/sample-pdf#result", code=303)


@bp.get("/dev/sample-pdf/file")
def download():
    """הורדת הקובץ האחרון, בשם `<כותרת> <DD-MM-YYYY>.pdf`."""
    last = _load_last()
    path = _pdf_path()
    if not last or not last.get("ok") or not path.is_file():
        return _error("אין קובץ", "עוד לא נוצר קובץ לדוגמה.", 404)
    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=last.get("filename") or "sample.pdf",
        max_age=0,
    )
