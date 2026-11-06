"""הבורר בדפדפן אמיתי (Chromium), בטלפון, בטאבלט ובמחשב — "סיום השלב" של שלב 2 ב-ROADMAP.

השרת הוא האפליקציה האמיתית במצב production, בתהליך הזה, עם דרייב מזויף (tests/fake_drive.py).
התרחיש עצמו ב-tests/browser/picker.mjs: חיפוש עם גרש, תיקייה עם יותר מ-50 תמונות
(שלושה עמודים), בחירה מכמה תתי-תיקיות, ניסיון לבחור תמונה 13, איפוס במעבר ללוקיישן אחר,
ותמונה ממוזערת שנחסמת בקישור הישיר ועוברת לנתיב שדרך השרת.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import threading
import zlib
from pathlib import Path

import pytest
from fake_drive import FakeDrive
from helpers import PASSWORD, production_env
from PIL import Image
from werkzeug.serving import make_server

import auth
import google_api
import picker_api
import thumbnails
from config import load_settings
from token_store import StoredGrant
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "browser" / "picker.mjs"


def _node_with_playwright() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    probe = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, env=_node_env())
    return node if probe.returncode == 0 else None


def _node_env() -> dict:
    env = dict(os.environ)
    npm = shutil.which("npm")
    if npm and "NODE_PATH" not in env:
        root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
        env["NODE_PATH"] = root
    return env


def _png(file_id: str) -> bytes:
    hue = zlib.crc32(file_id.encode()) % 255
    buf = io.BytesIO()
    Image.new("RGB", (220, 165), (hue, 140, 255 - hue)).save(buf, "PNG")
    return buf.getvalue()


class _FakeRaw:
    """כמו urllib3.HTTPResponse.read(amt, decode_content=...), שאליו thumbnails.py קורא."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, amt=None, decode_content=None):
        return self._buf.read(amt)


class _FakeThumbResponse:
    """מחקה את requests.Response ש-thumbnails.py קורא (stream=True ו-raw.read)."""

    def __init__(self, data: bytes):
        self.status_code = 200
        self.headers = {"Content-Type": "image/png"}
        self.raw = _FakeRaw(data)

    def close(self):
        pass


class _NoAuth:
    def before_request(self, request, method, url, headers):
        headers["Authorization"] = "Bearer test"


def _build_drive() -> tuple[FakeDrive, dict]:
    fake = FakeDrive()
    root = fake.add("לוקיישנים", owned=True, shared=False)
    villa = fake.add("וילה כרמל", parent=root)
    garden = fake.add("גינה", parent=villa)
    baths = fake.add("חדרי אמבטיה", parent=villa)
    zimmer = fake.add("צימר ג'ורג' בגליל", parent=root)
    fake.add("חדרי אמבטיה", parent=zimmer)
    for i in range(1, 6):
        fake.add(f"סלון {i}.jpg", "image/jpeg", villa)
    for i in range(1, 61):
        fake.add(f"גינה {i}.jpg", "image/jpeg", garden)
    for i in range(1, 4):
        fake.add(f"אמבטיה {i}.jpg", "image/jpeg", baths)
    for i in range(1, 5):
        fake.add(f"צימר {i}.jpg", "image/jpeg", zimmer)
    return fake, {"villa": villa, "garden": garden, "zimmer": zimmer}


@pytest.mark.slow
def test_picker_in_real_browser(data_dir, tmp_path, monkeypatch):
    node = _node_with_playwright()
    if node is None:
        pytest.skip("node with playwright is not available")

    app = create_app(load_settings(production_env(data_dir)))
    app.extensions["images2pdf"].token_store.save(
        StoredGrant("1//r", "owner@example.com", ("a",), "2026-11-06T00:00:00+00:00")
    )
    fake, ids = _build_drive()
    lock = threading.Lock()

    def drive():
        return fake

    monkeypatch.setattr(google_api, "get_drive_service", drive)
    monkeypatch.setattr(picker_api, "LIST_PAGE_SIZE", 25)  # 60 תמונות = שלושה עמודים
    monkeypatch.setattr(thumbnails, "get_credentials", lambda: _NoAuth())

    def fake_thumb_get(url, headers=None, timeout=None, stream=None):
        assert headers == {"Authorization": "Bearer test"}
        file_id = url.rsplit("/", 1)[-1].split("=")[0]
        with lock:
            return _FakeThumbResponse(_png(file_id))

    monkeypatch.setattr(thumbnails.requests, "get", fake_thumb_get)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        sample_png = tmp_path / "direct.png"
        sample_png.write_bytes(_png("direct"))
        shots = Path(os.environ.get("PICKER_SHOTS", tmp_path / "shots"))
        shots.mkdir(parents=True, exist_ok=True)
        args = {
            "base": base,
            "cookie": auth._cookie_token(PASSWORD),
            "cookieName": auth.COOKIE_NAME,
            "directPng": str(sample_png),
            "shots": str(shots),
            "ids": ids,
        }
        proc = subprocess.run(
            [node, str(SCRIPT), json.dumps(args)],
            capture_output=True, text=True, timeout=300, env={**_node_env(), "LANG": "C.UTF-8"},
        )
        print(proc.stdout)
        assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
        assert "ALL PICKER CHECKS PASSED" in proc.stdout
    finally:
        server.shutdown()
        thread.join(5)
