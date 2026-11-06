"""האפליקציה האמיתית, במצב production, מול דרייב מזויף — להרצה תחת gunicorn אמיתי.

משמשת את tests/test_pdf_jobs_gunicorn.py. תיקיית הנתונים מגיעה מ-DATA_DIR. כל הורדת
תמונה מחכה JOB_APP_DOWNLOAD_DELAY שניות, כדי שיהיה זמן לנתק את הלקוח באמצע עבודה.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_drive import FakeDrive  # noqa: E402
from helpers import production_env  # noqa: E402
from PIL import Image  # noqa: E402

import google_api  # noqa: E402
import pdf_jobs  # noqa: E402
from config import load_settings  # noqa: E402
from token_store import StoredGrant  # noqa: E402
from web_app import create_app  # noqa: E402

DELAY = float(os.environ.get("JOB_APP_DOWNLOAD_DELAY", "0.5"))

fake = FakeDrive()
LOCATION = fake.add("וילה כרמל")
IMAGES = []
for i in range(4):
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (i * 50, 100, 150)).save(buf, "JPEG")
    IMAGES.append(fake.add(f"סלון {i + 1}.jpg", "image/jpeg", LOCATION, content=buf.getvalue()))


def _download(file_id, dest, require_mimes=None):
    time.sleep(DELAY)
    data = fake.files_by_id[file_id]["content"]
    Path(dest).write_bytes(data)
    return len(data)


google_api.get_drive_service = lambda: fake
pdf_jobs.drive_download_to_file = _download

app = create_app(load_settings(production_env(Path(os.environ["DATA_DIR"]))))
app.extensions["images2pdf"].token_store.save(
    StoredGrant("1//r", "owner@example.com", ("a",), "2026-11-06T00:00:00+00:00")
)


def _ids(environ, start_response):
    """/_test/ids: מזהי הלוקיישן והתמונות, ו-/_test/pdfs: כמה קבצי PDF נשמרו בדרייב המזויף."""
    import json

    path = environ.get("PATH_INFO")
    if path == "/_test/ids":
        body = json.dumps({"location": LOCATION, "images": IMAGES}).encode()
    else:
        pdfs = [f["name"] for f in fake.files_by_id.values() if f["name"].endswith(".pdf")]
        body = json.dumps(pdfs, ensure_ascii=False).encode()
    start_response("200 OK", [("Content-Type", "application/json")])
    return [body]


_flask = app.wsgi_app


def _wsgi(environ, start_response):
    if environ.get("PATH_INFO", "").startswith("/_test/"):
        return _ids(environ, start_response)
    return _flask(environ, start_response)


app.wsgi_app = _wsgi
