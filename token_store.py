"""
token_store.py — שמירת הרשאת הגוגל מוצפנת בדיסק הקבוע.

מה נשמר: ה-refresh token, החשבון שאושר, ההרשאות שניתנו ומועד החיבור — יחד,
בקובץ אחד ובכתיבה אטומית אחת, כדי שלא ייווצר מצב של טוקן חדש עם פרטים ישנים.
ההצפנה היא Fernet (AES + HMAC). המפתח נגזר ב-HKDF מ-TOKEN_ENCRYPTION_KEY, ולכן
כל מחרוזת אקראית ארוכה מספיק עובדת, כולל הערך ש-Render מייצר (base64 רגיל,
שאינו בהכרח בפורמט ש-Fernet מקבל ישירות).
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from storage import atomic_write_bytes, file_lock

logger = logging.getLogger(__name__)

_HKDF_INFO = b"images2pdf/google-grant/v1"
_FILE_NAME = "google_grant.enc"


class TokenUnreadable(Exception):
    """קובץ ההרשאה קיים אבל אי אפשר לפענח אותו (המפתח הוחלף, או שהקובץ פגום)."""


@dataclass(frozen=True)
class StoredGrant:
    refresh_token: str
    email: str
    scopes: tuple[str, ...]
    connected_at: str  # ISO-8601, UTC
    # רק כשהמשתמש בחר בגוגל גישה מוגבלת בזמן. מקור:
    # https://developers.google.com/identity/protocols/oauth2/web-server (refresh_token_expires_in)
    refresh_token_expires_in: int | None = None

    def __repr__(self) -> str:  # שלא ידלוף ללוג דרך repr
        return f"StoredGrant(email=<hidden>, scopes={self.scopes!r}, connected_at={self.connected_at!r})"


def derive_fernet(key_material: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        key_material.encode("utf-8")
    )
    return Fernet(base64.urlsafe_b64encode(key))


class TokenStore:
    def __init__(self, data_dir: Path, key_material: str) -> None:
        self._path = data_dir / _FILE_NAME
        self._lock_path = data_dir / f".{_FILE_NAME}.lock"
        self._fernet = derive_fernet(key_material)

    @property
    def path(self) -> Path:
        return self._path

    def version(self) -> tuple[int, int, int] | None:
        """מזהה זול לגרסת הקובץ. None = אין הרשאה שמורה.

        כל שמירה יוצרת קובץ חדש (os.replace מקובץ זמני), ולכן ה-inode משתנה.
        """
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def load(self) -> StoredGrant | None:
        """None = אין הרשאה שמורה. זורק TokenUnreadable אם הקובץ קיים ולא נקרא."""
        try:
            blob = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(self._fernet.decrypt(blob))
        except InvalidToken as exc:
            raise TokenUnreadable("stored grant cannot be decrypted with the current key") from exc
        except ValueError as exc:  # JSON פגום אחרי פענוח מוצלח
            raise TokenUnreadable("stored grant is not valid JSON") from exc
        return _grant_from_payload(payload)

    def save(self, grant: StoredGrant) -> None:
        data = json.dumps(asdict(grant), ensure_ascii=False).encode("utf-8")
        with file_lock(self._lock_path):
            atomic_write_bytes(self._path, self._fernet.encrypt(data))

    def delete(self) -> bool:
        """מוחק את ההרשאה השמורה. True אם נמחק קובץ, False אם לא היה קובץ."""
        with file_lock(self._lock_path):
            try:
                self._path.unlink()
            except FileNotFoundError:
                return False
            return True

    def delete_if_version(self, version: tuple[int, int, int] | None) -> bool:
        """מוחק רק אם הקובץ הוא עדיין הגרסה שנכשלה.

        בלי התנאי הזה יש מרוץ: בקשה ישנה מקבלת invalid_grant על הטוקן הקודם
        בדיוק כשהמשתמש התחבר מחדש — ומוחקת את ההרשאה החדשה והתקינה.
        """
        if version is None:
            return False
        with file_lock(self._lock_path):
            if self.version() != version:
                return False
            try:
                self._path.unlink()
            except FileNotFoundError:
                return False
            return True


def _grant_from_payload(payload: object) -> StoredGrant:
    if not isinstance(payload, dict):
        raise TokenUnreadable("stored grant has an unexpected shape")
    refresh_token = payload.get("refresh_token")
    email = payload.get("email")
    scopes = payload.get("scopes")
    connected_at = payload.get("connected_at")
    expires_in = payload.get("refresh_token_expires_in")
    if not (isinstance(refresh_token, str) and refresh_token):
        raise TokenUnreadable("stored grant has no refresh token")
    if not isinstance(email, str) or not isinstance(connected_at, str):
        raise TokenUnreadable("stored grant is missing fields")
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise TokenUnreadable("stored grant has invalid scopes")
    if expires_in is not None and not (
        isinstance(expires_in, int) and not isinstance(expires_in, bool) and math.isfinite(expires_in) and expires_in >= 0
    ):
        expires_in = None
    return StoredGrant(
        refresh_token=refresh_token,
        email=email,
        scopes=tuple(scopes),
        connected_at=connected_at,
        refresh_token_expires_in=expires_in,
    )
