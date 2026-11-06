"""/dev/sample-pdf מקצה לקצה מול דרייב מזויף."""

from __future__ import annotations

import io
import json
import threading
import time
from urllib.parse import unquote

import pytest
from fake_drive import FakeDrive
from helpers import BASE_URL
from PIL import Image
from pypdf import PdfReader

import google_api
import pdf_jobs
import sample_pdf
from token_store import StoredGrant


def _jpeg_bytes(size, color):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def drive(app, monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(google_api, "get_drive_service", lambda: fake)
    folder = fake.add("וילה בכרמל")
    fake.add("תת-תיקייה", parent=folder)
    for i in range(14):
        fake.add(f"{i:02d}.jpg", "image/jpeg", folder, content=_jpeg_bytes((300 + i * 10, 200), (i * 15, 100, 150)))
    fake.add("broken.jpg", "image/jpeg", folder, content=b"not an image")
    fake.add("סרטון.mp4", "video/mp4", folder)
    fake.folder_id = folder

    def fake_download(file_id, dest, require_mimes=None):
        data = fake.files_by_id[file_id]["content"]
        with open(dest, "wb") as fh:
            fh.write(data)
        return len(data)

    # ההורדה עוברת דרך pdf_jobs.drive_fetch, המשותפת לדף הזה ולבורר
    monkeypatch.setattr(pdf_jobs, "drive_download_to_file", fake_download)
    # הדף בודק שיש חשבון מחובר
    app.extensions["images2pdf"].token_store.save(
        StoredGrant("1//r", "owner@example.com", ("a",), "2026-11-05T00:00:00+00:00")
    )
    return fake


def _last(app):
    return json.loads((app.extensions["images2pdf"].settings.data_dir / "dev_sample_last.json").read_text())


def test_creates_pdf_from_first_12_images_and_shows_metrics(app, logged_in, drive):
    resp = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": ""})
    assert resp.status_code == 303
    last = _last(app)
    assert last["ok"] and last["requested"] == 12 and last["processed"] == 12
    assert last["title"] == "וילה בכרמל"  # ברירת המחדל: שם התיקייה
    assert last["pages"] == 3
    assert set(last["seconds"]) == {"total", "fetch", "process", "build"}
    assert last["memory_mb"]["rss_peak"] is not None

    page = logged_in.get(f"{BASE_URL}/dev/sample-pdf").get_data(as_text=True)
    assert "הורדת הקובץ" in page and "שיא זיכרון" in page

    pdf = logged_in.get(f"{BASE_URL}/dev/sample-pdf/file")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    disposition = pdf.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert unquote(disposition.split("filename*=UTF-8''")[1]).startswith("וילה בכרמל ")
    reader = PdfReader(io.BytesIO(pdf.data))
    assert len(reader.pages) == 3
    uri = reader.pages[0]["/Annots"][0].get_object()["/A"]["/URI"]
    assert uri == f"https://drive.google.com/drive/folders/{drive.folder_id}"


def test_failed_image_is_reported_by_name(app, logged_in, drive, monkeypatch):
    # מעבירים את הקובץ הפגום לראש הרשימה
    broken = next(f for f in drive.files_by_id.values() if f["name"] == "broken.jpg")
    broken["name"] = "000-broken.jpg"
    logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id})
    last = _last(app)
    assert last["failed"] == [{"name": "000-broken.jpg", "reason": "הקובץ אינו תמונה שאפשר לקרוא."}]
    assert last["processed"] == 11
    assert "000-broken.jpg" in logged_in.get(f"{BASE_URL}/dev/sample-pdf").get_data(as_text=True)


def test_custom_title_and_validation(app, logged_in, drive):
    logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": "כותרת אחרת 7"})
    assert _last(app)["title"] == "כותרת אחרת 7"
    too_long = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": "א" * 121})
    assert too_long.status_code == 400
    bad_id = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": "x' or 1=1"})
    assert bad_id.status_code == 400
    missing = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": "doesNotExist1"})
    assert missing.status_code == 404


def test_parallel_requests_create_one_file_at_a_time(app, drive, monkeypatch):
    """שתי לחיצות באותו רגע: אחת יוצרת, השנייה מקבלת 409 — בלי יצירה כפולה."""
    from helpers import PASSWORD

    real = pdf_jobs.drive_download_to_file

    def slow_download(file_id, dest, require_mimes=None):
        time.sleep(0.05)
        return real(file_id, dest, require_mimes=require_mimes)

    monkeypatch.setattr(pdf_jobs, "drive_download_to_file", slow_download)
    clients = []
    for _ in range(2):
        c = app.test_client()
        c.post(f"{BASE_URL}/login", data={"password": PASSWORD})
        clients.append(c)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def click(c):
        barrier.wait()
        statuses.append(c.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id}).status_code)

    threads = [threading.Thread(target=click, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [303, 409]


def test_requires_login_and_no_file_before_first_run(client, logged_in):
    assert client.get(f"{BASE_URL}/dev/sample-pdf").status_code == 302
    assert logged_in.get(f"{BASE_URL}/dev/sample-pdf/file").status_code == 404


def test_title_without_drawable_characters_is_a_clear_400(app, logged_in, drive):
    resp = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": "🌊🌊"})
    assert resp.status_code == 400
    assert "אין אף תו שאפשר להציג" in resp.get_data(as_text=True)


def test_failed_result_save_does_not_leave_a_mismatched_pair(app, logged_in, drive, monkeypatch):
    """ה-PDF הוחלף אבל שמירת התוצאה נכשלה: התוצאה הישנה (עם השם הישן) לא נשארת."""
    logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": "ריצה ראשונה"})
    assert _last(app)["title"] == "ריצה ראשונה"

    def broken_write(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(sample_pdf, "atomic_write_bytes", broken_write)
    resp = logged_in.post(f"{BASE_URL}/dev/sample-pdf/run", data={"folder_id": drive.folder_id, "title": "ריצה שנייה"})
    assert resp.status_code == 500
    assert not (app.extensions["images2pdf"].settings.data_dir / "dev_sample_last.json").exists()
    assert logged_in.get(f"{BASE_URL}/dev/sample-pdf/file").status_code == 404
    assert "ריצה ראשונה" not in logged_in.get(f"{BASE_URL}/dev/sample-pdf").get_data(as_text=True)


@pytest.mark.parametrize("path", ["/dev/sample-pdf", "/dev/checks"])
def test_diagnostic_pages_show_a_broken_data_dir_instead_of_500(make_app, tmp_path, path):
    """DATA_DIR שמצביע על קובץ ולא על תיקייה: הדף מציג את הבעיה, ולא נופל."""
    from helpers import PASSWORD

    not_a_dir = tmp_path / "data-is-a-file"
    not_a_dir.write_text("oops")
    app = make_app(DATA_DIR=str(not_a_dir))
    c = app.test_client()
    assert c.post(f"{BASE_URL}/login", data={"password": PASSWORD}).status_code == 303
    resp = c.get(f"{BASE_URL}{path}")
    assert resp.status_code == 200
    assert "חסרות הגדרות" in resp.get_data(as_text=True)
