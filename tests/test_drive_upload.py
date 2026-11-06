"""העלאה לדרייב שאפשר לחדש (resumable), דרך googleapiclient האמיתי ושכבת רשת מזויפת.

הפרוטוקול כפי ש-googleapiclient מממש אותו (googleapiclient/http.py, HttpRequest.next_chunk
ו-process_response, גרסה 2.201.0): בקשה ראשונה עם ה-metadata שמחזירה `location`, ואז PUT
לכל חתיכה עם Content-Range. 308 עם `range` = עוד לא נגמר; 200 עם גוף JSON = הקובץ נוצר.
"""

from __future__ import annotations

import json
import re

import httplib2
import pytest
from googleapiclient.discovery import build

import google_api
from google_api import UPLOAD_CHUNK_BYTES, drive_upload_file

DATA = bytes(range(256)) * (9 * 1024 * 1024 // 256 + 3)  # קצת יותר מ-9MB: שלוש חתיכות


class ResumableHttp:
    def __init__(self, fail_puts: set[int] | None = None):
        self.start = None
        self.puts: list[str] = []
        self.received = bytearray()
        self.fail_puts = fail_puts or set()  # מספרי PUT (מ-1) שיחזירו 503

    def request(self, uri, method="GET", body=None, headers=None, redirections=5, connection_type=None):
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if method == "POST":
            self.start = {"uri": uri, "body": json.loads(body), "headers": headers}
            return httplib2.Response({"status": 200, "location": "https://upload.example/session-1"}), b""
        assert method == "PUT" and uri == "https://upload.example/session-1"
        self.puts.append(headers["content-range"])
        if len(self.puts) in self.fail_puts:
            return httplib2.Response({"status": 503}), b'{"error": {"code": 503, "message": "busy"}}'
        start, end, total = map(int, re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers["content-range"]).groups())
        if hasattr(body, "read"):  # googleapiclient שולח חתיכה מקובץ כ-_StreamSlice ולא כ-bytes
            body = body.read()
        assert len(body) == end - start + 1
        del self.received[start:]
        self.received += body
        if end + 1 < total:
            return httplib2.Response({"status": 308, "range": f"bytes=0-{end}"}), b""
        meta = {"id": "newFile123", "name": self.start["body"]["name"], "webViewLink": "https://drive.google.com/file/d/newFile123/view"}
        return httplib2.Response({"status": 200}), json.dumps(meta).encode()


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr("googleapiclient.http.time.sleep", lambda seconds: None)


@pytest.fixture
def service(monkeypatch):
    holder = {}

    def use(http):
        holder["svc"] = build("drive", "v3", http=http, cache_discovery=False)

    monkeypatch.setattr(google_api, "get_drive_service", lambda: holder["svc"])
    return use


def test_uploads_in_chunks_and_returns_the_new_file(tmp_path, service):
    src = tmp_path / "out.pdf"
    src.write_bytes(DATA)
    http = ResumableHttp()
    service(http)
    chunks = []
    meta = drive_upload_file(src, "וילה כרמל 06-11-2026.pdf", "application/pdf", "folder123", on_chunk=lambda: chunks.append(1))
    assert meta == {"id": "newFile123", "name": "וילה כרמל 06-11-2026.pdf", "webViewLink": "https://drive.google.com/file/d/newFile123/view"}
    assert "uploadType=resumable" in http.start["uri"]
    assert http.start["body"] == {"name": "וילה כרמל 06-11-2026.pdf", "parents": ["folder123"]}
    assert bytes(http.received) == DATA
    assert len(http.puts) == 3 and len(chunks) == 3
    assert UPLOAD_CHUNK_BYTES % (256 * 1024) == 0  # דרישת דרייב לחתיכה בהעלאה כזו


def test_transient_error_on_a_chunk_is_retried(tmp_path, service):
    src = tmp_path / "out.pdf"
    src.write_bytes(DATA)
    http = ResumableHttp(fail_puts={2})
    service(http)
    assert drive_upload_file(src, "a.pdf", "application/pdf", "folder123")["id"] == "newFile123"
    assert bytes(http.received) == DATA and len(http.puts) == 4


def test_the_source_file_is_closed_after_upload(tmp_path, service, monkeypatch):
    src = tmp_path / "out.pdf"
    src.write_bytes(b"%PDF small")
    opened = []
    real = google_api.MediaFileUpload

    def tracking(*args, **kwargs):
        media = real(*args, **kwargs)
        opened.append(media.stream())
        return media

    monkeypatch.setattr(google_api, "MediaFileUpload", tracking)
    service(ResumableHttp())
    drive_upload_file(src, "a.pdf", "application/pdf", "folder123")
    assert opened and opened[0].closed
