"""
storage.py — כתיבה בטוחה לדיסק הקבוע.

כל קובץ שהכלי שומר (ההרשאה המוצפנת, מצבי OAuth, מונה ניסיונות כניסה) עובר
דרך כאן: כתיבה לקובץ זמני באותה תיקייה, fsync, ואז `os.replace` — כך שקורא
אף פעם לא רואה קובץ חצי כתוב, גם אם התהליך נהרג באמצע.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterator

_thread_locks: dict[Path, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def data_dir_problem(data_dir: Path) -> str | None:
    """מחזיר תיאור בעברית אם אי אפשר לכתוב לתיקיית הנתונים, אחרת None."""
    if not data_dir.is_dir():
        return f"תיקיית הנתונים {data_dir} לא קיימת. ב-Render זה הדיסק הקבוע."
    if not os.access(data_dir, os.W_OK | os.X_OK):
        return f"אין הרשאת כתיבה לתיקיית הנתונים {data_dir}."
    return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """כותב את `data` ל-`path` באופן אטומי. זורק OSError בכשל."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    # בלי fsync על התיקייה, ה-rename עצמו עלול לא לשרוד נפילת חשמל.
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """נעילה בלעדית בין threads ובין תהליכים (flock).

    בין תהליכים: gunicorn מחליף worker אחרי `max_requests`, ולרגע יכולים לרוץ
    שני תהליכים. בין threads באותו תהליך: flock על אותו קובץ שנפתח פעמיים
    באותו תהליך לא תמיד חוסם, ולכן יש גם מנעול thread.
    """
    with _thread_locks_guard:
        tlock = _thread_locks.setdefault(lock_path, threading.Lock())
    with tlock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
