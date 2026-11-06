"""
pdf_job.py — יצירת PDF מקצה לקצה: הבאת כל תמונה, עיבוד, פריסה וכתיבה. כולל מדידות.

התמונות מעובדות אחת בכל פעם: מורידים לקובץ זמני, מעבדים לקובץ JPEG קטן, מוחקים את
המקור ומשחררים זיכרון — ורק אז עוברים לתמונה הבאה. בזיכרון אין אף פעם יותר מתמונה
אחת. תמונה שנכשלה (הורדה או עיבוד) מדווחת בשמה, והקובץ נוצר מהשאר.

איך מביאים קובץ מקור (`fetch`) מגיע מבחוץ: בשירות זו הורדה מדרייב, ובבדיקות העתקה
מקומית. כך בדיקת הזיכרון מריצה את אותו מסלול בדיוק, בלי רשת.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from image_processing import ImageProcessingError, ProcessedImage, process_image
from memory import MemoryPeaks, PeakSampler, release_memory
from pdf_builder import BuildResult, build_pdf
from pdf_layout import DocumentLayout, layout_document

logger = logging.getLogger(__name__)

MAX_IMAGES = 12

_thread_locks: dict[Path, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class GenerationLock:
    """יצירת PDF אחת בכל רגע, בכל התהליכים וה-threads (ROADMAP: "נעילה פשוטה מאפשרת
    יצירה של קובץ אחד בלבד בכל פעם"). גם הבורר וגם /dev/sample-pdf עוברים כאן.

    flock על קובץ בדיסק: gunicorn מחליף worker אחרי max_requests, ולרגע רצים שני
    תהליכים. הנעילה לא מחכה — מי שלא קיבל אותה מקבל תשובה מיד — ומשתחררת לבד כשהתהליך
    שמחזיק אותה מת (מערכת ההפעלה סוגרת את ה-fd). נמדד ב-Linux: fd שני על אותו קובץ,
    גם באותו תהליך, מקבל EAGAIN. מנעול ה-thread שומר על אותה התנהגות גם במערכות שבהן
    flock באותו תהליך לא חוסם (ראו storage.file_lock).
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "generate.lock"
        with _thread_locks_guard:
            self._tlock = _thread_locks.setdefault(self._path, threading.Lock())
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            raise RuntimeError("already held")
        if not self._tlock.acquire(blocking=False):
            return False
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        except BaseException:
            self._tlock.release()
            raise
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            self._tlock.release()
            return False
        except BaseException:
            os.close(fd)
            self._tlock.release()
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._tlock.release()

    def is_free(self) -> bool:
        """האם אף אחד לא מייצר עכשיו. בודק ומשחרר מיד."""
        if not self.try_acquire():
            return False
        self.release()
        return True


@dataclass(frozen=True)
class SourceItem:
    id: str
    name: str


class FetchError(Exception):
    """הבאת קובץ המקור נכשלה. `reason` בעברית למשתמש."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class JobResult:
    ok: bool
    layout: DocumentLayout | None = None
    build: BuildResult | None = None
    failed: list[tuple[str, str]] = field(default_factory=list)  # (שם, סיבה)
    processed: int = 0
    source_bytes: int = 0
    pdf_bytes: int = 0
    seconds_fetch: float = 0.0
    seconds_process: float = 0.0
    seconds_build: float = 0.0
    seconds_total: float = 0.0
    memory: MemoryPeaks | None = None


def run_pdf_job(
    items: Sequence[SourceItem],
    fetch: Callable[[SourceItem, Path], int],
    *,
    title: str,
    link_url: str | None,
    out_path: Path,
    work_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> JobResult:
    """`fetch(item, dest)` מוריד את המקור ל-dest ומחזיר את מספר הבתים, או זורק FetchError.

    ה-PDF נכתב קודם לקובץ זמני בתוך work_dir, ומועבר ל-out_path רק אם הכתיבה הצליחה.
    `on_progress(done, total)` נקרא אחרי כל תמונה, גם כשהיא נכשלה.
    """
    if not 1 <= len(items) <= MAX_IMAGES:
        raise ValueError(f"between 1 and {MAX_IMAGES} images are required")
    work_dir.mkdir(parents=True, exist_ok=True)
    result = JobResult(ok=False)
    started = time.monotonic()
    processed: list[ProcessedImage] = []

    with PeakSampler() as sampler:
        try:
            for i, item in enumerate(items):
                src = work_dir / f"source-{i:02d}.bin"
                dst = work_dir / f"image-{i:02d}.jpg"
                t0 = time.monotonic()
                try:
                    result.source_bytes += fetch(item, src)
                except FetchError as exc:
                    result.failed.append((item.name, exc.reason))
                    if on_progress is not None:
                        on_progress(i + 1, len(items))
                    continue
                finally:
                    result.seconds_fetch += time.monotonic() - t0
                t1 = time.monotonic()
                try:
                    processed.append(process_image(src, dst, item.name))
                except ImageProcessingError as exc:
                    result.failed.append((item.name, exc.reason))
                finally:
                    src.unlink(missing_ok=True)
                    release_memory()
                    result.seconds_process += time.monotonic() - t1
                if on_progress is not None:
                    on_progress(i + 1, len(items))

            result.processed = len(processed)
            if processed:
                t2 = time.monotonic()
                layout = layout_document([p.aspect for p in processed])
                tmp_pdf = work_dir / "out.pdf"
                build = build_pdf(tmp_pdf, processed, layout, title, link_url)
                shutil.move(str(tmp_pdf), str(out_path))
                result.layout = layout
                result.build = build
                result.pdf_bytes = out_path.stat().st_size
                result.seconds_build = time.monotonic() - t2
                result.ok = True
        finally:
            release_memory()
    result.memory = sampler.result()
    result.seconds_total = time.monotonic() - started
    return result
