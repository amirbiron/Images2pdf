"""הנתיב /api/drive/thumbnail/<id> שדרך השרת, כולל הגודל הגדול לתצוגה המוגדלת (שלב 3)."""

from __future__ import annotations

import io

import pytest
from fake_drive import FakeDrive
from helpers import BASE_URL

import google_api
import thumbnails
from token_store import StoredGrant


class _Raw:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def read(self, amt=None, decode_content=None):
        return self._buf.read(amt)


class _Resp:
    status_code = 200
    headers = {"Content-Type": "image/jpeg"}

    def __init__(self):
        self.raw = _Raw(b"\xff\xd8jpeg")

    def close(self):
        pass


class _NoAuth:
    def before_request(self, request, method, url, headers):
        headers["Authorization"] = "Bearer test"


@pytest.fixture
def fetched(app, monkeypatch):
    fake = FakeDrive()
    folder = fake.add("וילה")
    fake.image = fake.add("סלון.jpg", "image/jpeg", folder)
    monkeypatch.setattr(google_api, "get_drive_service", lambda: fake)
    monkeypatch.setattr(thumbnails, "get_credentials", lambda: _NoAuth())
    urls = []

    def fake_get(url, headers=None, timeout=None, stream=None):
        urls.append(url)
        return _Resp()

    monkeypatch.setattr(thumbnails.requests, "get", fake_get)
    app.extensions["images2pdf"].token_store.save(StoredGrant("1//r", "owner@example.com", ("a",), "2026-11-06T00:00:00+00:00"))
    fake.urls = urls
    return fake


def test_default_size_is_googles_link_as_is(logged_in, fetched):
    resp = logged_in.get(f"{BASE_URL}/api/drive/thumbnail/{fetched.image}")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert fetched.urls[-1].endswith("=s220")


def test_large_size_for_the_viewer(logged_in, fetched):
    resp = logged_in.get(f"{BASE_URL}/api/drive/thumbnail/{fetched.image}?size=large")
    assert resp.status_code == 200
    assert fetched.urls[-1].endswith(f"=s{thumbnails.LARGE_SIZE}")
    assert fetched.urls[-1].startswith("https://lh3.googleusercontent.com/")


def test_unknown_size_is_refused(logged_in, fetched):
    assert logged_in.get(f"{BASE_URL}/api/drive/thumbnail/{fetched.image}?size=huge").status_code == 400
    assert fetched.urls == []


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://lh3.googleusercontent.com/abc=s220", "https://lh3.googleusercontent.com/abc=s1600"),
        ("https://lh3.googleusercontent.com/abc", "https://lh3.googleusercontent.com/abc"),  # בלי סיומת: כמו שהוא
        ("https://lh3.googleusercontent.com/a=s220/b", "https://lh3.googleusercontent.com/a=s220/b"),
    ],
)
def test_sized_link_only_changes_a_trailing_size(link, expected):
    assert thumbnails.sized_link(link, 1600) == expected
