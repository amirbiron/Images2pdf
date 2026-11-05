"""
oauth_state.py — ה-`state` של OAuth נשמר בצד השרת, ונצרך פעם אחת בלבד.

למה לא ב-session של Flask: session בעוגייה נדרס על ידי התשובה האחרונה שהגיעה
לדפדפן, ובקשה מקבילה יכולה למחוק או להחזיר ערך שכבר נצרך. לכן ה-state יושב
בדיסק, וה"פעם אחת" נאכף שם בפעולה אטומית אחת: `os.rename`. מבין שתי בקשות
שמנסות לצרוך אותו state, רק אחת מצליחה בשינוי השם, והשנייה מקבלת
FileNotFoundError — גם בין threads וגם בין תהליכים.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import time
from pathlib import Path

from storage import atomic_write_bytes

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600
STATE_MIN_LEN = 16
STATE_MAX_LEN = 128


class OAuthStateStore:
    def __init__(self, data_dir: Path, *, ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        self._dir = data_dir / "oauth_states"
        self._ttl = ttl_seconds

    def _path_for(self, state: str) -> Path:
        # שם הקובץ הוא hash של ה-state: ה-state מגיע מהכתובת, ולכן אסור שישמש
        # כנתיב (`../`), וגם אין סיבה לשמור אותו גולמי בדיסק.
        return self._dir / hashlib.sha256(state.encode("utf-8")).hexdigest()

    def create(self) -> str:
        state = secrets.token_urlsafe(32)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_expired()
        atomic_write_bytes(self._path_for(state), json.dumps({"created_at": time.time()}).encode())
        return state

    def consume(self, state: object) -> bool:
        """True רק בפעם הראשונה שה-state נצרך, ורק אם לא פג תוקפו."""
        if not isinstance(state, str) or not STATE_MIN_LEN <= len(state) <= STATE_MAX_LEN:
            return False
        src = self._path_for(state)
        claimed = src.with_name(f"{src.name}.claimed-{secrets.token_hex(8)}")
        try:
            os.rename(src, claimed)
        except FileNotFoundError:
            return False
        try:
            payload = json.loads(claimed.read_bytes())
        except (OSError, ValueError):
            logger.warning("OAuth state file was unreadable; treating the state as invalid")
            return False
        finally:
            claimed.unlink(missing_ok=True)
        created = payload.get("created_at") if isinstance(payload, dict) else None
        if not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created):
            return False
        age = time.time() - created
        return 0 <= age <= self._ttl

    def _cleanup_expired(self) -> None:
        now = time.time()
        try:
            entries = list(self._dir.iterdir())
        except OSError:
            logger.warning("Could not list OAuth state directory for cleanup", exc_info=True)
            return
        for entry in entries:
            try:
                # קבצי claimed שנשארו (תהליך שנהרג באמצע) נמחקים גם הם אחרי ה-TTL.
                if now - entry.stat().st_mtime > self._ttl:
                    entry.unlink(missing_ok=True)
            except OSError:
                # קובץ שנמחק במקביל או שאין הרשאה אליו: הניקוי הוא best-effort,
                # וקובץ שנשאר לא פוגע בנכונות — consume בודק את הגיל בעצמו.
                continue
