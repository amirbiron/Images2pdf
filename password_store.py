"""
password_store.py — סיסמת הכניסה שהמשתמש בחר, וכניסה ראשונה עם הסיסמה מ-WEB_PANEL_SECRET.

שני מצבים, ותקלה:
- **set**: יש סיסמה שמורה בתוקף. רק היא פותחת את הכלי, והסיסמה מ-WEB_PANEL_SECRET כבר לא.
- **setup**: אין סיסמה שמורה בתוקף. הסיסמה מ-WEB_PANEL_SECRET עובדת פעם אחת: אחרי כניסה
  איתה בוחרים סיסמה חדשה, ורק אז אפשר להשתמש בכלי.
- **תקלה** (PasswordStorageError): הדיסק לא זמין, או שקובץ הסיסמה לא נקרא. חוסמים את הכניסה.
  לא חוזרים לסיסמה מ-WEB_PANEL_SECRET: קובץ פגום היה פותח שוב את הדלת לסיסמה הישנה.

איפוס בלי טרמינל: לצד הסיסמה נשמרת "טביעה" של הערך של WEB_PANEL_SECRET מהרגע שבו היא
נבחרה. כשמחליפים את הערך ב-Render, הטביעה לא מתאימה, והאיפוס נרשם בקובץ (ולא רק מחושב):
הסיסמה השמורה מתבטלת לצמיתות, והערך החדש הוא שוב סיסמה חד-פעמית. גם חזרה לערך קודם היא
החלפה, ולא מחזירה לתוקף את הסיסמה שאופסה. הבדיקה רצה גם בעליית השרת (web_app.create_app),
כך שהאיפוס נרשם גם אם אף אחד לא נכנס בין ההחלפה לחזרה.

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
import contextlib
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
from typing import Iterator

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


# אורכים מדויקים, כמו שהקוד כותב אותם. כל אורך אחר הוא קובץ פגום, ולא "WEB_PANEL_SECRET
# הוחלף": טביעה פגומה שהייתה נקראת כהחלפה הייתה מחזירה לתוקף את הסיסמה הזמנית.
SALT_BYTES = 16
HASH_BYTES = _DKLEN
SESSION_KEY_BYTES = 32
MARKER_NAME = "password.initialized"


@dataclass(frozen=True)
class StoredPassword:
    salt: bytes
    hash: bytes
    session_key: bytes
    changed_at: str


@dataclass(frozen=True)
class _Fingerprint:
    """טביעה (scrypt) של ערך WEB_PANEL_SECRET. מאפשרת לזהות שהערך הוחלף, בלי לשמור אותו."""

    salt: bytes
    hash: bytes


@dataclass(frozen=True)
class _Record:
    """תוכן password.json: סיסמה שנבחרה ("password"), או איפוס ("reset") — אחרי שהערך של
    WEB_PANEL_SECRET הוחלף, ועד שנבחרת סיסמה חדשה. שניהם קשורים לערך שבתוקף כשנכתבו."""

    kind: str
    env: _Fingerprint
    password: StoredPassword | None


def _exact(value: object, length: int) -> bytes:
    data = _unb64(value)
    if len(data) != length:
        raise ValueError("wrong length")
    return data


class PasswordStore:
    """הקבצים בתיקיית הנתונים:

    - password.json: הסיסמה שנבחרה, או רשומת איפוס. מרגע שנוצר הוא לא נמחק, רק מוחלף.
    - password.initialized: סימן שהכלי כבר הוגדר, עם טביעת הערך של WEB_PANEL_SECRET שהיה בתוקף
      בכתיבה האחרונה. אם password.json נעלם והערך לא הוחלף מאז — הכניסה נחסמת, ולא חוזרת
      לסיסמה הזמנית. הדרך לצאת מזה היא אותה דרך איפוס: להחליף את WEB_PANEL_SECRET.

    כל החלפה של WEB_PANEL_SECRET נרשמת בקובץ (רשומת איפוס), ולא רק מחושבת בכל בקשה. כך החזרה
    לערך קודם לא מחזירה לתוקף את הסיסמה שאופסה: היא נחשבת להחלפה נוספת, והערך הוא שוב חד-פעמי.

    ערוץ כשל אחד: כל תקלת דיסק או קובץ פגום הן PasswordStorageError.
    """

    def __init__(self, data_dir: Path, env_secret: str) -> None:
        self._data_dir = data_dir
        self._path = data_dir / FILE_NAME
        self._marker_path = data_dir / MARKER_NAME
        self._lock_path = data_dir / ".password.lock"
        self._env_secret = env_secret
        self._cache_lock = threading.Lock()
        self._cache: tuple[tuple[int, int, int], _Record] | None = None
        # האם טביעה מתאימה לערך הנוכחי של WEB_PANEL_SECRET. הערך קבוע בתהליך (משתנה סביבה),
        # ולכן מחשבים פעם אחת לכל טביעה, ולא scrypt בכל בקשה.
        self._env_match: dict[tuple[bytes, bytes], bool] = {}

    # ─── המצב ─────────────────────────────────────────────────

    def current(self) -> StoredPassword | None:
        """הסיסמה השמורה, או None במצב setup. זורק PasswordStorageError בתקלה."""
        return self._resolve(locked=False)

    def _resolve(self, *, locked: bool) -> StoredPassword | None:
        if data_dir_problem(self._data_dir) is not None:
            raise PasswordStorageError("data directory is not usable")
        record = self._load()
        if record is None:
            marker = self._load_marker()
            if marker is None:
                return None  # כניסה ראשונה אמיתית: הכלי עוד לא הוגדר
            if self._matches_env(marker):
                logger.error("The password file is missing although a password was set; login is blocked")
                raise PasswordStorageError("password file is missing")
            self._reset(locked=locked)  # הקובץ חסר, אבל WEB_PANEL_SECRET הוחלף מאז: זה איפוס
            return None
        if not self._matches_env(record.env):
            self._reset(locked=locked)
            return None
        return record.password  # None ברשומת איפוס: הערך הנוכחי הוא סיסמה חד-פעמית

    def _matches_env(self, fp: _Fingerprint) -> bool:
        key = (fp.salt, fp.hash)
        with self._cache_lock:
            known = self._env_match.get(key)
        if known is None:
            known = hmac.compare_digest(_kdf(self._env_secret, fp.salt), fp.hash)
            with self._cache_lock:
                self._env_match[key] = known
        return known

    # ─── קריאה ────────────────────────────────────────────────

    def _load(self) -> _Record | None:
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.error("The password file cannot be read; login is blocked until it is fixed")
            raise PasswordStorageError("cannot stat the password file") from exc
        version = (st.st_mtime_ns, st.st_ino, st.st_size)
        with self._cache_lock:
            if self._cache is not None and self._cache[0] == version:
                return self._cache[1]
        try:
            data = json.loads(self._path.read_bytes())
        except FileNotFoundError:
            return None  # הוחלף בין stat לקריאה (os.replace); הקריאה הבאה תראה את החדש
        except (OSError, ValueError) as exc:
            logger.error("The password file cannot be read; login is blocked until it is fixed")
            raise PasswordStorageError("unreadable password file") from exc
        try:
            record = self._parse(data)
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("The password file is malformed; login is blocked until it is fixed")
            raise PasswordStorageError("malformed password file") from exc
        with self._cache_lock:
            self._cache = (version, record)
        return record

    @staticmethod
    def _parse(data: object) -> _Record:
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("kdf") != "scrypt":
            raise ValueError("unknown format")
        if any(data.get(k) != v for k, v in _KDF.items()):
            raise ValueError("unknown parameters")
        env = _Fingerprint(salt=_exact(data["env_salt"], SALT_BYTES), hash=_exact(data["env_hash"], HASH_BYTES))
        kind = data.get("kind")
        if kind == "reset":
            return _Record(kind="reset", env=env, password=None)
        if kind != "password":
            raise ValueError("unknown kind")
        password = StoredPassword(
            salt=_exact(data["salt"], SALT_BYTES),
            hash=_exact(data["hash"], HASH_BYTES),
            session_key=_exact(data["session_key"], SESSION_KEY_BYTES),
            changed_at=str(data.get("changed_at", "")),
        )
        return _Record(kind="password", env=env, password=password)

    def _load_marker(self) -> _Fingerprint | None:
        try:
            data = json.loads(self._marker_path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.error("The password marker cannot be read; login is blocked until it is fixed")
            raise PasswordStorageError("unreadable password marker") from exc
        try:
            if not isinstance(data, dict) or data.get("v") != 1:
                raise ValueError("unknown format")
            return _Fingerprint(salt=_exact(data["env_salt"], SALT_BYTES), hash=_exact(data["env_hash"], HASH_BYTES))
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("The password marker is malformed; login is blocked until it is fixed")
            raise PasswordStorageError("malformed password marker") from exc

    # ─── בדיקה ─────────────────────────────────────────────────

    @staticmethod
    def verify(stored: StoredPassword, password: str) -> bool:
        return hmac.compare_digest(_kdf(password, stored.salt), stored.hash)

    def verify_one_time(self, password: str) -> bool:
        """הסיסמה מ-WEB_PANEL_SECRET (מצב setup). השוואה על bytes, אחרי אותו נרמול."""
        return hmac.compare_digest(normalize(password).encode("utf-8"), normalize(self._env_secret).encode("utf-8"))

    # ─── כתיבה ─────────────────────────────────────────────────

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(file_lock(self._lock_path))
            except OSError as exc:
                logger.error("Could not lock the password file", exc_info=True)
                raise PasswordStorageError("cannot lock the password file") from exc
            yield

    def set_initial(self, new_password: str) -> StoredPassword:
        """הבחירה הראשונה, אחרי כניסה עם הסיסמה החד-פעמית. אטומי: בתוך נעילה בודקים שעדיין
        אין סיסמה בתוקף. שני מכשירים שבוחרים באותו רגע — רק אחד מצליח (PasswordAlreadySet)."""
        with self._lock():
            if self._resolve(locked=True) is not None:
                raise PasswordAlreadySet()
            stored = self._write_password(new_password)
        logger.info("A password was chosen; the one-time password no longer works")
        return stored

    def change(self, current_password: str, new_password: str) -> StoredPassword:
        """החלפה עם הסיסמה הנוכחית. מפתח חדש, ולכן מכשירים אחרים מתנתקים."""
        with self._lock():
            stored = self._resolve(locked=True)
            if stored is None or not self.verify(stored, current_password):
                raise WrongPassword()
            new = self._write_password(new_password)
        logger.info("The password was changed; other devices must sign in again")
        return new

    def _reset(self, *, locked: bool) -> None:
        """רושם שהערך של WEB_PANEL_SECRET הוחלף: הסיסמה השמורה מבוטלת לצמיתות."""
        if locked:
            self._write_reset()
            return
        with self._lock():
            # בדיקה חוזרת בתוך הנעילה: worker אחר אולי כבר רשם את האיפוס, או שכבר נבחרה סיסמה.
            record = self._load()
            if record is not None and self._matches_env(record.env):
                return
            self._write_reset()

    def _write_reset(self) -> None:
        env = self._fingerprint_now()
        self._write_state({"kind": "reset", "reset_at": _now()}, env)
        logger.warning("WEB_PANEL_SECRET was changed: the stored password is void, the new value is one-time")

    def _write_password(self, new_password: str) -> StoredPassword:
        problem = password_problem(new_password)
        if problem:
            raise ValueError(problem)
        salt = secrets.token_bytes(SALT_BYTES)
        stored = StoredPassword(
            salt=salt,
            hash=_kdf(new_password, salt),
            session_key=secrets.token_bytes(SESSION_KEY_BYTES),
            changed_at=_now(),
        )
        fields = {
            "kind": "password",
            "salt": _b64(stored.salt),
            "hash": _b64(stored.hash),
            "session_key": _b64(stored.session_key),
            "changed_at": stored.changed_at,
        }
        self._write_state(fields, self._fingerprint_now())
        return stored

    def _fingerprint_now(self) -> _Fingerprint:
        salt = secrets.token_bytes(SALT_BYTES)
        return _Fingerprint(salt=salt, hash=_kdf(self._env_secret, salt))

    def _write_state(self, fields: dict, env: _Fingerprint) -> None:
        record = {"v": 1, "kdf": "scrypt", **_KDF, "env_salt": _b64(env.salt), "env_hash": _b64(env.hash), **fields}
        try:
            atomic_write_bytes(self._path, json.dumps(record).encode("utf-8"))
        except OSError as exc:
            logger.error("Could not save the password file", exc_info=True)
            raise PasswordStorageError("cannot write the password file") from exc
        marker = {"v": 1, "env_salt": _b64(env.salt), "env_hash": _b64(env.hash)}
        try:
            atomic_write_bytes(self._marker_path, json.dumps(marker).encode("utf-8"))
        except OSError:
            # הסיסמה עצמה נשמרה ותקינה. בלי הסימן, רק ההגנה מפני password.json שנעלם נחלשת:
            # קובץ שייעלם ייחשב לכניסה ראשונה. לכן תיעוד בלוג ולא כישלון של הבחירה.
            logger.error("Could not save the password marker", exc_info=True)
        with self._cache_lock:
            self._cache = None
            self._env_match[(env.salt, env.hash)] = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
