"""הורדה מדרייב לקובץ, דרך googleapiclient האמיתי ושכבת רשת מזויפת שמתעדת כל בקשה."""

from __future__ import annotations

import json
import re

import httplib2
import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import google_api
from google_api import DOWNLOAD_CHUNK_BYTES, DownloadTooLarge, drive_download_to_file

DATA = bytes(range(256)) * (10 * 1024 * 1024 // 256 + 7)  # קצת יותר מ-10MB


class RecordingHttp:
    """מחקה httplib2.Http: files.get מחזיר size, ו-alt=media מחזיר את הטווח המבוקש."""

    def __init__(self, data: bytes, *, size_override: int | None = None, fail_on_chunk: int | None = None):
        self.data = data
        self.size_override = size_override
        self.fail_on_chunk = fail_on_chunk
        self.ranges: list[str] = []

    def request(self, uri, method="GET", body=None, headers=None, redirections=5, connection_type=None):
        headers = headers or {}
        if "alt=media" not in uri:
            size = self.size_override if self.size_override is not None else len(self.data)
            return httplib2.Response({"status": 200}), json.dumps({"size": str(size)}).encode()
        rng = headers.get("range") or headers.get("Range")
        self.ranges.append(rng)
        if self.fail_on_chunk is not None and len(self.ranges) == self.fail_on_chunk:
            return httplib2.Response({"status": 500}), b'{"error": {"code": 500, "message": "boom"}}'
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", rng).groups())
        chunk = self.data[start : end + 1]
        resp = httplib2.Response({"status": 206, "content-range": f"bytes {start}-{start + len(chunk) - 1}/{len(self.data)}"})
        return resp, chunk


@pytest.fixture
def service(monkeypatch):
    holder = {}

    def use(http):
        holder["svc"] = build("drive", "v3", http=http, cache_discovery=False)

    monkeypatch.setattr(google_api, "get_drive_service", lambda: holder["svc"])
    return use


def test_downloads_to_disk_in_4mb_chunks(tmp_path, service):
    http = RecordingHttp(DATA)
    service(http)
    dest = tmp_path / "out.bin"
    assert drive_download_to_file("abc123", dest) == len(DATA)
    assert dest.read_bytes() == DATA
    expected = [f"bytes={s}-{s + DOWNLOAD_CHUNK_BYTES - 1}" for s in range(0, len(DATA), DOWNLOAD_CHUNK_BYTES)]
    assert http.ranges == expected
    assert DOWNLOAD_CHUNK_BYTES == 4 * 1024 * 1024


def test_oversized_file_is_refused_before_downloading(tmp_path, service):
    http = RecordingHttp(DATA, size_override=google_api.MAX_DOWNLOAD_BYTES + 1)
    service(http)
    with pytest.raises(DownloadTooLarge):
        drive_download_to_file("abc123", tmp_path / "out.bin")
    assert http.ranges == []
    assert not (tmp_path / "out.bin").exists()


def test_partial_file_is_removed_when_download_fails(tmp_path, service):
    http = RecordingHttp(DATA, fail_on_chunk=2)
    service(http)
    dest = tmp_path / "out.bin"
    with pytest.raises(HttpError):
        drive_download_to_file("abc123", dest)
    assert not dest.exists()


def test_invalid_id_is_refused(tmp_path):
    with pytest.raises(ValueError):
        drive_download_to_file("x' or 1=1", tmp_path / "out.bin")
