"""שמירת ההרשאה: מוצפנת, אטומית, ולא נמחקת בטעות אחרי חיבור מחדש."""

from __future__ import annotations

import os
import stat

import pytest

from token_store import StoredGrant, TokenStore, TokenUnreadable

GRANT = StoredGrant(
    refresh_token="1//refresh-token-value",
    email="owner@example.com",
    scopes=("https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive.readonly"),
    connected_at="2026-11-05T10:00:00+00:00",
)


def test_roundtrip_and_encryption_at_rest(data_dir):
    store = TokenStore(data_dir, "k" * 44)
    store.save(GRANT)
    raw = store.path.read_bytes()
    assert b"refresh-token-value" not in raw
    assert b"owner@example.com" not in raw
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.load() == GRANT
    assert [p.name for p in data_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_render_generated_key_format_works(data_dir):
    # הצורה ש-Render מייצר: base64 רגיל של 256 ביט (מהתיעוד של blueprint-spec).
    store = TokenStore(data_dir, "B0jrphAPOY7pg92AN0c9MN4yecczLMdwnx4OkA1KFUk=")
    store.save(GRANT)
    assert store.load() == GRANT


def test_wrong_key_is_unreadable_not_a_crash(data_dir):
    TokenStore(data_dir, "k" * 44).save(GRANT)
    with pytest.raises(TokenUnreadable):
        TokenStore(data_dir, "z" * 44).load()


def test_missing_file_means_not_connected(data_dir):
    store = TokenStore(data_dir, "k" * 44)
    assert store.load() is None
    assert store.version() is None
    assert store.delete() is False


def test_delete_if_version_does_not_remove_a_newer_grant(data_dir):
    store = TokenStore(data_dir, "k" * 44)
    store.save(GRANT)
    old_version = store.version()
    store.save(StoredGrant(**{**GRANT.__dict__, "refresh_token": "1//newer"}))  # חיבור מחדש
    assert store.version() != old_version
    assert store.delete_if_version(old_version) is False
    assert store.load().refresh_token == "1//newer"
    assert store.delete_if_version(store.version()) is True
    assert store.load() is None


def test_repr_hides_secrets():
    text = repr(GRANT)
    assert "refresh-token-value" not in text and "owner@example.com" not in text
