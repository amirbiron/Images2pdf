"""
memory.py — מדידת זיכרון ושחרורו. `release_memory` הועתקה מ-rss_probe.py של
Social Publisher (סעיף 7 בקובץ החילוץ), עם אותו נימוק.

השירות רץ עם מגבלה של 512MB. glibc לא מחזיר למערכת זיכרון ששוחרר, ולכן אחרי כל
תמונה קוראים ל-gc.collect ול-malloc_trim. בלי זה ה-RSS נשאר גבוה עד מחזור ה-worker.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# מטמון של libc כדי לא לטעון אותו בכל קריאה. False = נכשל פעם אחת, ולא מנסים שוב.
_libc_cache: "object | bool | None" = None

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
# cgroup v2 ואחריו v1. Render מגביל את הזיכרון ברמת ה-container, ולכן זה המספר
# שקובע אם התהליך ייהרג — לא ה-RSS של התהליך לבדו.
_CGROUP_CURRENT = (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"))


def release_memory() -> None:
    """gc.collect() ואז libc.malloc_trim(0), כדי להחזיר למערכת זיכרון ששוחרר."""
    global _libc_cache

    try:
        gc.collect()
    except Exception as e:  # noqa: BLE001 — כמו במקור: לא להפיל את הבקשה בגלל ניקוי
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
        except Exception as e:  # noqa: BLE001
            # בכוונה רחב (כמו במקור): זה רץ בתוך finally, וחריגה כאן הייתה מסתירה
            # את התשובה המקורית. libc חסרה (למשל musl) היא מצב צפוי, לא תקלה.
            logger.debug(f"libc.malloc_trim unavailable: {e}")
            _libc_cache = False
            return

    try:
        _libc_cache.malloc_trim(0)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"malloc_trim failed: {e}")


def rss_bytes() -> int | None:
    """ה-RSS הנוכחי של התהליך, או None אם אי אפשר לקרוא אותו (לא Linux)."""
    try:
        with open("/proc/self/statm", "rb") as fh:
            return int(fh.read().split()[1]) * _PAGE_SIZE
    except (OSError, ValueError, IndexError):
        return None


def cgroup_bytes() -> int | None:
    """הזיכרון של כל ה-container (כולל page cache), או None אם אין cgroup נגיש."""
    for path in _CGROUP_CURRENT:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


@dataclass(frozen=True)
class MemoryPeaks:
    rss_start: int | None
    rss_peak: int | None
    cgroup_start: int | None
    cgroup_peak: int | None
    samples: int


class PeakSampler:
    """דוגם את ה-RSS ואת ה-cgroup כל `interval` שניות, ומחזיר את השיא.

    שיא אמיתי בין שתי דגימות יכול להתפספס, ולכן המרווח קצר (50ms). זו מדידה,
    לא הגבלה.
    """

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="memory-peak-sampler", daemon=True)
        self._rss_start = rss_bytes()
        self._cg_start = cgroup_bytes()
        self._rss_peak = self._rss_start
        self._cg_peak = self._cg_start
        self._samples = 0

    def __enter__(self) -> "PeakSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(2)
        self._sample()  # דגימה אחרונה אחרי הסוף

    def _sample(self) -> None:
        r = rss_bytes()
        c = cgroup_bytes()
        if r is not None:
            self._rss_peak = r if self._rss_peak is None else max(self._rss_peak, r)
        if c is not None:
            self._cg_peak = c if self._cg_peak is None else max(self._cg_peak, c)
        self._samples += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            time.sleep(self._interval)

    def result(self) -> MemoryPeaks:
        return MemoryPeaks(self._rss_start, self._rss_peak, self._cg_start, self._cg_peak, self._samples)
