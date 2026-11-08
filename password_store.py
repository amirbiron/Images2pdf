"""
password_store.py — סיסמת הכניסה שהמשתמש בחר, וכניסה ראשונה עם הסיסמה מ-WEB_PANEL_SECRET.

שני מצבים, ותקלה:
- **set**: יש סיסמה שמורה בתוקף. רק היא פותחת את הכלי, והסיסמה מ-WEB_PANEL_SECRET כבר לא.
- **setup**: אין סיסמה שמורה בתוקף. הסיסמה מ-WEB_PANEL_SECRET עובדת פעם אחת: אחרי כניסה
  איתה בוחרים סיסמה חדשה, ורק אז אפשר להשתמש בכלי.
- **תקלה** (PasswordStorageError): הדיסק לא זמין, או שקובץ הסיסמה לא נקרא. חוסמים את הכניסה.
  לא חוזרים לסיסמה מ-WEB_PANEL_SECRET: קובץ פגום היה פותח שוב את הדלת לסיסמה הישנה.

איפוס בלי טרמינל: לצד הסיסמה נשמרת "טביעה" של הערך של WEB_PANEL_SECRET מהרגע שבו היא
נבחרה. כשמחליפים את הערך ב-Render, הטביעה לא מתאימה, הסיסמה השמורה מתבטלת, והערך החדש
הוא שוב סיסמה חד-פעמית.

"מחובר" נקבע לפי מפתח אקראי (session_key) שנשמר עם הסיסמה. העוגייה נגזרת ממנו, ובכל
החלפת סיסמה נוצר מפתח חדש — כך כל המכשירים האחרים צריכים להיכנס מחדש.

הסיסמה נשמרת כ-hash של scrypt עם salt אקראי: N=2^14, r=8, p=5 (16MiB), אחת מהתצורות
השקולות ש-OWASP ממליץ עליהן. התצורה הראשית שם, N=2^17, דורשת 128MiB לכל בדיקה, וזה
יותר מדי לצד יצירת PDF בשרת של 512MB. נמדד: כ-220ms ו-16MB לבדיקה.
מקור: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
לפני ה-hash הסיסמה עוברת נרמול NFC, והאורך נמדד בתווים, כך שאותה סיסמה בעברית שהוקלדה
בטלפון אחר נותנת אותה תוצאה. מקור: https://pages.nist.gov/800-63-4/sp800-63b.html
("the verifier SHOULD apply ... Normalization Form Canonical Composition (NFC)").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from storage import atomic_write_bytes, data_dir_problem, file_lock

logger = logging.getLogger(__name__)

PASSWORD_MIN_CHARS = 8
PASSWORD_MAX_CHARS = 256
FILE_NAME = "password.json"

_KDF = {"n": 2**14, "r": 8, "p": 5}
_MAXMEM = 64 * 1024 * 1024  # מעל 16MiB שהתצורה צריכה, כדי ש-OpenSSL לא ידחה אותה
_DKLEN = 32


class PasswordStorageError(Exception):
    """אי אפשר לדעת מה הסיסמה: הדיסק לא זמין או שהקובץ פגום. חוסמים את הכניסה."""


class PasswordAlreadySet(Exception):
    """מישהו כבר בחר סיסמה (למשל ממכשיר אחר), ולכן הסיסמה החד-פעמית כבר לא בתוקף."""


class WrongPassword(Exception):
    """הסיסמה הנוכחית שהוקלדה בטופס השינוי שגויה."""


def normalize(password: str) -> str:
    return unicodedata.normalize("NFC", password)


def password_problem(password: object) -> str | None:
    """תיאור בעברית של מה שלא תקין בסיסמה חדשה, או None. כל תו מותר, כולל עברית ורווחים."""
    if not isinstance(password, str) or password == "":
        return "צריך להקליד סיסמה."
    length = len(normalize(password))
    if length < PASSWORD_MIN_CHARS:
        return f"הסיסמה צריכה להכיל לפחות {PASSWORD_MIN_CHARS} תווים."
    if length > PASSWORD_MAX_CHARS:
        # נדחית ולא נחתכת: סיסמה חתוכה הייתה נשמרת אחרת ממה שהמשתמש חושב שבחר.
        return f"הסיסמה יכולה להכיל עד {PASSWORD_MAX_CHARS} תווים."
    return None


def _kdf(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(normalize(password).encode("utf-8"), salt=salt, maxmem=_MAXMEM, dklen=_DKLEN, **_KDF)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("not a string")
    return base64.b64decode(value, validate=True)


@dataclass(frozen=True)
class StoredPassword:
    salt: bytes
    hash: bytes
    env_salt: bytes
    env_hash: bytes
    session_key: bytes
    changed_at: str


class PasswordStore:
    def __init__(self, data_dir: Path, env_secret: str) -> None:
        self._data_dir = data_dir
        self._path = data_dir / FILE_NAME
        self._lock_path = data_dir / ".password.lock"
        self._env_secret = env_secret
        self._cache_lock = threading.Lock()
        self._cache: tuple[tuple[int, int, int], StoredPassword | None] | None = None
        # האם הטביעה בקובץ מתאימה לערך הנוכחי של WEB_PANEL_SECRET. הערך הזה קבוע בתהליך
        # (משתנה סביבה), ולכן מחשבים פעם אחת לכל טביעה, ולא scrypt בכל בקשה.
        self._env_match: dict[tuple[bytes, bytes], bool] = {}

    # ─── קריאה ────────────────────────────────────────────────

    def current(self) -> StoredPassword | None:
        """הסיסמה השמורה, או None במצב setup. זורק PasswordStorageError בתקלה."""
        if data_dir_problem(self._data_dir) is not None:
            raise PasswordStorageError("data directory is not usable")
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PasswordStorageError("cannot stat the password file") from exc
        version = (st.st_mtime_ns, st.st_ino, st.st_size)
        with self._cache_lock:
            if self._cache is not None and self._cache[0] == version:
                return self._cache[1]
        stored = self._read()
        with self._cache_lock:
            self._cache = (version, stored)
        return stored

    def _read(self) -> StoredPassword | None:
        try:
            data = json.loads(self._path.read_bytes())
        except FileNotFoundError:
            return None  # נמחק בין stat לקריאה: מצב setup
        except (OSError, ValueError) as exc:
            logger.error("The password file cannot be read; login is blocked until it is fixed")
            raise PasswordStorageError("unreadable password file") from exc
        try:
            if not isinstance(data, dict) or data.get("v") != 1 or data.get("kdf") != "scrypt":
                raise ValueError("unknown format")
            if any(data.get(k) != v for k, v in _KDF.items()):
                raise ValueError("unknown parameters")
            stored = StoredPassword(
                salt=_unb64(data["salt"]),
                hash=_unb64(data["hash"]),
                env_salt=_unb64(data["env_salt"]),
                env_hash=_unb64(data["env_hash"]),
                session_key=_unb64(data["session_key"]),
                changed_at=str(data.get("changed_at", "")),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("The password file is malformed; login is blocked until it is fixed")
            raise PasswordStorageError("malformed password file") from exc
        if len(stored.session_key) < 32 or len(stored.salt) < 16 or len(stored.env_salt) < 16:
            logger.error("The password file is malformed; login is blocked until it is fixed")
            raise PasswordStorageError("malformed password file")
        if not self._matches_env(stored):
            return None
        return stored

    def _matches_env(self, stored: StoredPassword) -> bool:
        key = (stored.env_salt, stored.env_hash)
        with self._cache_lock:
            known = self._env_match.get(key)
        if known is None:
            known = hmac.compare_digest(_kdf(self._env_secret, stored.env_salt), stored.env_hash)
            with self._cache_lock:
                self._env_match[key] = known
            if not known:
                logger.warning("WEB_PANEL_SECRET was changed: the stored password is void, the new value is one-time")
        return known

    # ─── בדיקה ─────────────────────────────────────────────────

    @staticmethod
    def verify(stored: StoredPassword, password: str) -> bool:
        return hmac.compare_digest(_kdf(password, stored.salt), stored.hash)

    def verify_one_time(self, password: str) -> bool:
        """הסיסמה מ-WEB_PANEL_SECRET (מצב setup). השוואה על bytes, אחרי אותו נרמול."""
        return hmac.compare_digest(normalize(password).encode("utf-8"), normalize(self._env_secret).encode("utf-8"))

    # ─── כתיבה ─────────────────────────────────────────────────

    def set_initial(self, new_password: str) -> StoredPassword:
        """הבחירה הראשונה, אחרי כניסה עם הסיסמה החד-פעמית. אטומי: בתוך נעילה בודקים שעדיין
        אין סיסמה בתוקף. שני מכשירים שבוחרים באותו רגע — רק אחד מצליח (PasswordAlreadySet)."""
        with file_lock(self._lock_path):
            if self.current() is not None:
                raise PasswordAlreadySet()
            stored = self._write(new_password)
        logger.info("A password was chosen; the one-time password no longer works")
        return stored

    def change(self, current_password: str, new_password: str) -> StoredPassword:
        """החלפה עם הסיסמה הנוכחית. מפתח חדש, ולכן מכשירים אחרים מתנתקים."""
        with file_lock(self._lock_path):
            stored = self.current()
            if stored is None or not self.verify(stored, current_password):
                raise WrongPassword()
            new = self._write(new_password)
        logger.info("The password was changed; other devices must sign in again")
        return new

    def _write(self, new_password: str) -> StoredPassword:
        problem = password_problem(new_password)
        if problem:
            raise ValueError(problem)
        salt, env_salt = secrets.token_bytes(16), secrets.token_bytes(16)
        stored = StoredPassword(
            salt=salt,
            hash=_kdf(new_password, salt),
            env_salt=env_salt,
            env_hash=_kdf(self._env_secret, env_salt),
            session_key=secrets.token_bytes(32),
            changed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        record = {
            "v": 1,
            "kdf": "scrypt",
            **_KDF,
            "salt": _b64(stored.salt),
            "hash": _b64(stored.hash),
            "env_salt": _b64(stored.env_salt),
            "env_hash": _b64(stored.env_hash),
            "session_key": _b64(stored.session_key),
            "changed_at": stored.changed_at,
        }
        atomic_write_bytes(self._path, json.dumps(record).encode("utf-8"))  # זורק OSError בכשל
        with self._cache_lock:
            self._cache = None
            self._env_match[(stored.env_salt, stored.env_hash)] = True
        return stored
