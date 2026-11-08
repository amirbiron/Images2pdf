"""קלט חיצוני: מזהים, escape בשאילתה, ובדיקת המארח של thumbnailLink."""

from __future__ import annotations

import pytest
from helpers import BASE_URL

import google_api
from config import is_valid_drive_id
from google_api import escape_query_value, is_allowed_thumbnail_url


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ג'ורג'", "ג\\'ורג\\'"),
        ("a\\b", "a\\\\b"),
        ("x\\'y", "x\\\\\\'y"),  # לוכסן ואחריו גרש: קודם הלוכסן, אחר כך הגרש
        ("חדרי אמבטיה", "חדרי אמבטיה"),
    ],
)
def test_escape_query_value(text, expected):
    assert escape_query_value(text) == expected


def test_escape_rejects_non_strings():
    with pytest.raises(TypeError):
        escape_query_value(["x"])


@pytest.mark.parametrize("value", [None, 5, ["a"], {"a": 1}, b"abc", "", "a b", "a'b", "a/b", "x" * 201, "ג"])
def test_invalid_drive_ids(value):
    assert is_valid_drive_id(value) is False


def test_valid_drive_id():
    assert is_valid_drive_id("1AbCdEfGhIjKlMnOpQrStUvWxYz-12345_x") is True  # מזהה מומצא


@pytest.mark.parametrize(
    "url, allowed",
    [
        ("https://lh3.googleusercontent.com/drive-storage/abc=s220", True),
        ("https://googleusercontent.com/x", True),
        ("https://lh3.googleusercontent.com:443/x", True),
        ("https://evilgoogleusercontent.com/x", False),
        ("https://lh3.googleusercontent.com.evil.example/x", False),
        ("http://lh3.googleusercontent.com/x", False),
        ("https://lh3.googleusercontent.com:8443/x", False),
        ("https://user:pw@lh3.googleusercontent.com/x", False),
        ("javascript:alert(1)", False),
        (None, False),
        (["https://lh3.googleusercontent.com/x"], False),
    ],
)
def test_thumbnail_host_allowlist(url, allowed):
    assert is_allowed_thumbnail_url(url) is allowed


class _FakeList:
    def __init__(self, captured):
        self.captured = captured

    def list(self, **kwargs):
        self.captured.update(kwargs)
        return self

    def execute(self):
        return {"files": [{"id": "a", "name": "ג'ורג'"}, "garbage"], "nextPageToken": 5}


class _FakeDrive:
    def __init__(self, captured):
        self.captured = captured

    def files(self):
        return _FakeList(self.captured)


def test_search_query_is_escaped_and_response_is_validated(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(google_api, "get_drive_service", lambda: _FakeDrive(captured))
    result = google_api.drive_search_folders("ג'ורג")
    assert captured["q"] == (
        "mimeType = 'application/vnd.google-apps.folder' and name contains 'ג\\'ורג' and trashed = false"
    )
    assert result["files"] == [{"id": "a", "name": "ג'ורג'"}]
    assert result["nextPageToken"] is None  # לא מחרוזת → מתעלמים


def test_list_folder_rejects_bad_ids_before_querying(monkeypatch):
    monkeypatch.setattr(google_api, "get_drive_service", lambda: pytest.fail("must not query"))
    with pytest.raises(ValueError):
        google_api.drive_list_folder("x' or 'a'='a")


def test_list_folder_only_folders_and_images(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(google_api, "get_drive_service", lambda: _FakeDrive(captured))
    google_api.drive_list_folder("abc123")
    q = captured["q"]
    assert "'abc123' in parents" in q
    assert "image/jpeg" in q and "image/png" in q and "video" not in q
