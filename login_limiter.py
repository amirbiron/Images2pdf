"""
login_limiter.py — הגבלת ניסיונות כניסה.

המונה שמור בדיסק ולא בזיכרון: gunicorn ממחזר את ה-worker אחרי `max_requests`,
ומונה בזיכרון היה מתאפס בכל מחזור — תוקף היה שולח מאתיים בקשות זבל ומקבל
מונה נקי. אם הדיסק לא זמין, נופלים למונה בזיכרון (עדיף מכלום) ומתעדים בלוג.

הניסיון נרשם *לפני* בדיקת הסיסמה, בתוך הנעילה, ונמחק אם הסיסמה נכונה. כך גם
מאה בקשות מקבילות לא עוברות את התקרה: אין חלון בין "בדקתי שיש מקום" לבין
"רשמתי שניסיתי".
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path

from storage import atomic_write_bytes, data_dir_problem, file_lock

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
WINDOW_SECONDS = 15 * 60


class LoginLimiter:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "login_attempts.json"
        self._lock_path = data_dir / ".login_attempts.lock"
        self._max = max_attempts
        self._window = window_seconds
        self._memory: list[float] = []
        self._memory_lock = threading.Lock()
        self._warned_memory = False

    def try_acquire(self) -> tuple[float | None, int]:
        """מנסה לרשום ניסיון.

        מחזיר `(ticket, 0)` כשמותר לבדוק סיסמה, או `(None, retry_after_seconds)`
        כשהתקרה מלאה. את ה-ticket מעבירים ל-`release_success` אם הסיסמה נכונה.
        """
        if data_dir_problem(self._data_dir) is not None:
            return self._try_acquire_memory()
        try:
            with file_lock(self._lock_path):
                # השעה נלקחת בתוך הנעילה: thread שלקח אותה לפני שחיכה היה רואה
                # ניסיונות שנרשמו בזמן ההמתנה כ"עתידיים", ומסנן אותם מהספירה.
                now = time.time()
                attempts = self._read(now)
                if len(attempts) >= self._max:
                    return None, self._retry_after(attempts, now)
                attempts.append(now)
                self._write(attempts)
                return now, 0
        except OSError:
            logger.warning("Login limiter storage failed; falling back to in-memory counting", exc_info=True)
            return self._try_acquire_memory()

    def release_success(self, ticket: float) -> None:
        """סיסמה נכונה: מוחקים את המונה כולו (המשתמש האמיתי נכנס)."""
        with self._memory_lock:
            self._memory.clear()
        if data_dir_problem(self._data_dir) is not None:
            return
        try:
            with file_lock(self._lock_path):
                self._write([])
        except OSError:
            # הכניסה עצמה הצליחה; מונה שלא התאפס רק מקצר את המכסה עד שיפוג החלון.
            logger.warning("Could not reset the login limiter after a successful login", exc_info=True)

    def _try_acquire_memory(self) -> tuple[float | None, int]:
        if not self._warned_memory:
            logger.warning("Login limiter is counting in memory; the count resets when the worker restarts")
            self._warned_memory = True
        with self._memory_lock:
            now = time.time()
            self._memory[:] = [t for t in self._memory if self._in_window(t, now)]
            if len(self._memory) >= self._max:
                return None, self._retry_after(self._memory, now)
            self._memory.append(now)
            return now, 0

    def _retry_after(self, attempts: list[float], now: float) -> int:
        oldest = min(attempts)
        return max(1, math.ceil(self._window - (now - oldest)))

    def _read(self, now: float) -> list[float]:
        try:
            raw = json.loads(self._path.read_bytes())
        except FileNotFoundError:
            return []
        except ValueError:
            logger.warning("Login limiter file was corrupt; starting a new count")
            return []
        if not isinstance(raw, list):
            return []
        return [
            float(t)
            for t in raw
            if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and self._in_window(t, now)
        ]

    def _in_window(self, t: float, now: float) -> bool:
        # סובלנות של דקה קדימה: שעון המערכת יכול לזוז אחורה מעט (סנכרון NTP),
        # ואסור שניסיון שנרשם רגע לפני כן ייעלם מהספירה בגלל זה.
        return -60 <= now - t < self._window

    def _write(self, attempts: list[float]) -> None:
        atomic_write_bytes(self._path, json.dumps(attempts).encode())
