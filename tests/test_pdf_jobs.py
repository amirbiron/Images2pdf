"""יצירת PDF מהבורר (שלב 3) מול דרייב מזויף: זרימה מלאה, לחיצה כפולה, שמירה שנכשלה,
הרשאה שבוטלה, עבודה שנקטעה, וולידציה. "סיום השלב" של שלב 3 ב-ROADMAP."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from fake_drive import FakeDrive, _http_error
from helpers import BASE_URL, sign_in
from PIL import Image
from pypdf import PdfReader

import google_api
import pdf_jobs
from google_api import WrongFileType
from pdf_job import GenerationLock
from token_store import StoredGrant

GRANT = StoredGrant(
    refresh_token="1//refresh",
    email="owner@example.com",
    scopes=("https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive.readonly"),
    connected_at="2026-11-06T10:00:00+00:00",
)
JOB = "0123456789abcdef0123456789abcdef"


def _jpeg(size, color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _job_id(n: int) -> str:
    return f"{n:032x}"


@pytest.fixture
def drive(app, monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(google_api, "get_drive_service", lambda: fake)
    location = fake.add("וילה כרמל")
    garden = fake.add("גינה", parent=location)
    fake.images = []
    for i in range(5):
        fake.images.append(fake.add(f"סלון {i + 1}.jpg", "image/jpeg", location, content=_jpeg((400, 300), (i * 40, 90, 160))))
    for i in range(3):
        fake.images.append(fake.add(f"גינה {i + 1}.jpg", "image/jpeg", garden, content=_jpeg((300, 400), (200, i * 50, 60))))
    fake.video = fake.add("סרטון.mp4", "video/mp4", location, content=b"\x00" * 100)
    fake.broken = fake.add("פגום.jpg", "image/jpeg", location, content=b"not an image")
    fake.location = location
    fake.download_delay = 0.0
    fake.downloads = []

    def fake_download(file_id, dest, require_mimes=None):
        f = fake.files_by_id.get(file_id)
        if f is None:
            raise _http_error(404, "notFound")
        if require_mimes and f["mimeType"] not in require_mimes:
            raise WrongFileType()
        time.sleep(fake.download_delay)
        fake.downloads.append(file_id)
        with open(dest, "wb") as fh:
            fh.write(f["content"])
        return len(f["content"])

    monkeypatch.setattr(pdf_jobs, "drive_download_to_file", fake_download)
    app.extensions["images2pdf"].token_store.save(GRANT)
    return fake


def _body(drive, ids=None, job_id=JOB, title="וילה כרמל"):
    ids = ids if ids is not None else drive.images[:4]
    return {
        "id": job_id,
        "location_id": drive.location,
        "title": title,
        "images": [{"id": i, "name": drive.files_by_id[i]["name"] if i in drive.files_by_id else "x.jpg"} for i in ids],
    }


def _pdfs_in_drive(drive) -> list[dict]:
    return [f for f in drive.files_by_id.values() if f["name"].endswith(".pdf")]


def _client(app):
    return sign_in(app, app.test_client())


# ─── הזרימה המלאה ────────────────────────────────────────────

def test_full_flow_creates_saves_and_serves_the_pdf(app, logged_in, drive):
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive))
    assert resp.status_code == 200
    job = resp.get_json()
    assert job["status"] == "done" and job["error"] is None and job["failed"] == []
    assert job["progress"] == {"done": 4, "total": 4}
    assert job["saved_to_drive"] is True and job["drive_error"] is None
    assert job["drive_url"].startswith("https://drive.google.com/")
    assert job["file_url"] == f"/api/pdf/jobs/{JOB}/file"

    # נשמר בתיקייה "קבצי PDF", בשם `<כותרת> <DD-MM-YYYY>.pdf`
    folders = [f for f in drive.files_by_id.values() if f["name"] == "קבצי PDF"]
    assert len(folders) == 1
    saved = _pdfs_in_drive(drive)
    assert len(saved) == 1 and saved[0]["parents"] == [folders[0]["id"]]
    assert saved[0]["name"] == job["filename"] and saved[0]["name"].startswith("וילה כרמל ")

    # ההורדה: אותו קובץ, עם השם בעברית והקישור לתיקיית הלוקיישן בכותרת
    pdf = logged_in.get(f"{BASE_URL}{job['file_url']}")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    assert pdf.data == saved[0]["content"]
    assert unquote(pdf.headers["Content-Disposition"].split("filename*=UTF-8''")[1]) == job["filename"]
    reader = PdfReader(io.BytesIO(pdf.data))
    uri = reader.pages[0]["/Annots"][0].get_object()["/A"]["/URI"]
    assert uri == f"https://drive.google.com/drive/folders/{drive.location}"

    # המצב נשאר זמין אחרי הסוף (לדפדפן שחוזר אחרי שהמסך ננעל)
    again = logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}")
    assert again.status_code == 200 and again.get_json() == job


def test_images_from_subfolders_keep_the_selection_order(app, logged_in, drive):
    order = [drive.images[6], drive.images[0], drive.images[5]]
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive, ids=order))
    assert resp.get_json()["status"] == "done"
    assert drive.downloads == order


def test_failed_images_are_reported_by_name_and_the_rest_is_created(app, logged_in, drive):
    ids = [drive.images[0], drive.video, drive.broken, drive.images[1]]
    job = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive, ids=ids)).get_json()
    assert job["status"] == "done"
    names = {f["name"]: f["reason"] for f in job["failed"]}
    assert set(names) == {"סרטון.mp4", "פגום.jpg"}
    assert "JPEG או PNG" in names["סרטון.mp4"]
    assert drive.video not in drive.downloads  # סוג הקובץ נבדק לפני ההורדה


def test_all_images_failed_is_a_failed_job_without_a_file(app, logged_in, drive):
    job = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive, ids=[drive.broken])).get_json()
    assert job["status"] == "failed" and job["error"] == "no_images" and job["file_url"] is None
    assert _pdfs_in_drive(drive) == []


def test_location_that_disappeared(app, logged_in, drive):
    body = _body(drive)
    body["location_id"] = "doesNotExist123"
    job = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=body).get_json()
    assert job["status"] == "failed" and job["error"] == "location_not_found"


# ─── שמירה בדרייב שנכשלה: ההורדה עדיין זמינה ─────────────────

def test_drive_save_failure_still_offers_the_download(app, logged_in, drive):
    drive.upload_error = _http_error(500, "backendError")
    job = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).get_json()
    assert job["status"] == "done" and job["drive_error"] == "save_failed"
    assert job["saved_to_drive"] is False and job["drive_url"] is None
    pdf = logged_in.get(f"{BASE_URL}{job['file_url']}")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")


def test_pdf_folder_is_created_once_and_recreated_after_trash(app, logged_in, drive):
    for n in (1, 2):
        assert logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive, job_id=_job_id(n))).get_json()["saved_to_drive"]
    folders = [f for f in drive.files_by_id.values() if f["name"] == "קבצי PDF"]
    assert len(folders) == 1
    folders[0]["trashed"] = True
    assert logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive, job_id=_job_id(3))).get_json()["saved_to_drive"]
    live = [f for f in drive.files_by_id.values() if f["name"] == "קבצי PDF" and not f["trashed"]]
    assert len(live) == 1 and live[0]["id"] != folders[0]["id"]


# ─── לחיצה כפולה ויצירה אחת בכל פעם ──────────────────────────

def test_same_job_id_again_returns_the_existing_job(app, logged_in, drive):
    first = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).get_json()
    second = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive))
    assert second.status_code == 200 and second.get_json() == first
    assert len(_pdfs_in_drive(drive)) == 1


def _parallel_posts(app, bodies):
    """שולח את כל הבקשות באותו רגע, כל אחת מ-thread ומלקוח משלה."""
    clients = [_client(app) for _ in bodies]
    barrier = threading.Barrier(len(bodies))
    results: list = [None] * len(bodies)

    def go(i):
        barrier.wait()
        resp = clients[i].post(f"{BASE_URL}/api/pdf/jobs", json=bodies[i])
        results[i] = (resp.status_code, resp.get_json())

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(bodies))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def test_two_fast_clicks_with_the_same_id_create_one_file(app, drive):
    drive.download_delay = 0.05
    results = _parallel_posts(app, [_body(drive), _body(drive)])
    assert sorted(status for status, _ in results) == [200, 202]
    running = next(body for status, body in results if status == 202)
    assert running["status"] == "running"
    assert len(_pdfs_in_drive(drive)) == 1
    assert len(drive.downloads) == 4  # כל תמונה הורדה פעם אחת


def test_two_different_jobs_at_once_run_one_at_a_time(app, drive):
    drive.download_delay = 0.05
    results = _parallel_posts(app, [_body(drive, job_id=_job_id(1)), _body(drive, job_id=_job_id(2))])
    assert sorted(status for status, _ in results) == [200, 409]
    assert next(body for status, body in results if status == 409) == {"error": "busy"}
    assert len(_pdfs_in_drive(drive)) == 1


def test_generation_lock_blocks_another_process(data_dir):
    """gunicorn מחליף worker אחרי max_requests, ולרגע רצים שני תהליכים."""
    lock = GenerationLock(data_dir)
    assert lock.try_acquire()
    try:
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from pdf_job import GenerationLock;"
            "print(GenerationLock(Path(sys.argv[2])).try_acquire())"
        )
        root = str(Path(__file__).resolve().parent.parent)
        out = subprocess.run([sys.executable, "-c", code, root, str(data_dir)], capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "False"
    finally:
        lock.release()
    assert GenerationLock(data_dir).is_free()


# ─── עבודה שנקטעה ומצב "רצה" ─────────────────────────────────

def _write_running(app, job_id=JOB):
    jobs = app.extensions["images2pdf"].settings.data_dir / "jobs"
    jobs.mkdir(exist_ok=True)
    record = {"id": job_id, "status": "running", "stage": "images", "title": "x", "filename": "x.pdf",
              "done": 2, "total": 4, "pdf": False, "failed": [], "updated_at": time.time()}
    (jobs / f"{job_id}.json").write_text(json.dumps(record))


def test_running_job_reports_progress_while_the_lock_is_held(app, logged_in, drive):
    _write_running(app)
    lock = GenerationLock(app.extensions["images2pdf"].settings.data_dir)
    assert lock.try_acquire()
    try:
        resp = logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}")
        assert resp.status_code == 202
        assert resp.get_json()["status"] == "running" and resp.get_json()["progress"] == {"done": 2, "total": 4}
        assert logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}/file").status_code == 404
    finally:
        lock.release()


def test_running_job_without_a_live_process_is_marked_interrupted(app, logged_in, drive):
    """פריסה חדשה או תהליך שנהרג באמצע: הרישום נשאר "רצה", אבל אף אחד לא מחזיק את הנעילה."""
    _write_running(app)
    resp = logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}")
    assert resp.status_code == 200 and resp.get_json()["status"] == "failed"
    assert resp.get_json()["error"] == "interrupted"


# ─── הרשאה שבוטלה ─────────────────────────────────────────────

@pytest.fixture
def revoked(monkeypatch):
    """שרת הטוקנים עונה invalid_grant, דרך הקוד האמיתי של google-auth (כמו ב-test_google_errors)."""
    monkeypatch.setattr(
        "google.oauth2._client._token_endpoint_request_no_throw",
        lambda *a, **k: (False, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, False),
    )
    google_api.forget_cached_clients()
    yield
    google_api.forget_cached_clients()


def test_revoked_grant_fails_the_job_and_asks_to_reconnect(app, logged_in, revoked):
    store = app.extensions["images2pdf"].token_store
    store.save(GRANT)
    body = {"id": JOB, "location_id": "abcDEF123_-", "title": "וילה", "images": [{"id": "img123456", "name": "a.jpg"}]}
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=body)
    assert resp.status_code == 401 and resp.get_json() == {"error": "google_reconnect_required"}
    assert store.load() is None  # ההרשאה המתה נמחקה, ודף הבית יציג את מסך החיבור
    status = logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}").get_json()
    assert status["status"] == "failed" and status["error"] == "google_reconnect_required"


def _real_invalid_grant(monkeypatch):
    """RefreshError שנבנה בקוד האמיתי של google-auth מתשובת invalid_grant, ולא ביד."""
    import google.auth.transport.requests
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    monkeypatch.setattr(
        "google.oauth2._client._token_endpoint_request_no_throw",
        lambda *a, **k: (False, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, False),
    )
    creds = Credentials(token=None, refresh_token="1//r", token_uri="https://oauth2.googleapis.com/token",
                        client_id="id", client_secret="secret")
    with pytest.raises(RefreshError) as info:
        creds.refresh(google.auth.transport.requests.Request())
    assert google_api.refresh_error_kind(info.value) == "invalid_grant"
    return info.value


def test_grant_revoked_during_save_keeps_the_download(app, logged_in, drive, monkeypatch):
    drive.upload_error = _real_invalid_grant(monkeypatch)
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive))
    assert resp.status_code == 401
    status = logged_in.get(f"{BASE_URL}/api/pdf/jobs/{JOB}").get_json()
    assert status["status"] == "done" and status["drive_error"] == "google_reconnect_required"
    assert logged_in.get(f"{BASE_URL}{status['file_url']}").status_code == 200


# ─── ניקוי ─────────────────────────────────────────────────────

def test_finished_jobs_older_than_a_day_are_removed(app, logged_in, drive):
    jobs = app.extensions["images2pdf"].settings.data_dir / "jobs"
    jobs.mkdir(exist_ok=True)
    old = _job_id(7)
    (jobs / f"{old}.json").write_text(json.dumps({"id": old, "status": "done", "pdf": True, "updated_at": time.time() - 25 * 3600}))
    (jobs / f"{old}.pdf").write_bytes(b"%PDF old")
    assert logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).status_code == 200
    assert not (jobs / f"{old}.json").exists() and not (jobs / f"{old}.pdf").exists()
    assert (jobs / f"{JOB}.pdf").exists()


def test_interrupted_jobs_nobody_asks_about_are_cleaned_too(app, logged_in, drive):
    """תהליך שנהרג באמצע עבודה משאיר רשומה "רצה". אם אף דפדפן לא שואל עליה, היא מסומנת
    כנקטעת כשעבודה חדשה מתחילה, ונמחקת לפי אותם כללים של עבודה שהסתיימה."""
    jobs = app.extensions["images2pdf"].settings.data_dir / "jobs"
    jobs.mkdir(exist_ok=True)
    old, recent = _job_id(8), _job_id(9)
    for job_id, age in ((old, 25 * 3600), (recent, 60)):
        (jobs / f"{job_id}.json").write_text(json.dumps(
            {"id": job_id, "status": "running", "pdf": False, "updated_at": time.time() - age}))
    assert logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).status_code == 200
    assert not (jobs / f"{old}.json").exists()
    kept = json.loads((jobs / f"{recent}.json").read_text())
    assert kept["status"] == "failed" and kept["error"] == "interrupted"
    assert json.loads((jobs / f"{JOB}.json").read_text())["status"] == "done"  # העבודה החדשה לא נגעה בעצמה


def test_cleanup_never_touches_a_job_that_is_really_running(app, logged_in, drive):
    """עבודה חיה מחזיקה את נעילת היצירה (כאן: מוחזקת בבדיקה, כמו תהליך אחר). בקשה חדשה
    מקבלת busy לפני הניקוי, ולכן הרשומה של העבודה החיה נשארת "רצה"."""
    _write_running(app, _job_id(5))
    lock = GenerationLock(app.extensions["images2pdf"].settings.data_dir)
    assert lock.try_acquire()
    try:
        assert logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).status_code == 409
        jobs = app.extensions["images2pdf"].settings.data_dir / "jobs"
        assert json.loads((jobs / f"{_job_id(5)}.json").read_text())["status"] == "running"
    finally:
        lock.release()


# ─── ולידציה ───────────────────────────────────────────────────

def _bad(drive, **changes):
    body = _body(drive)
    body.update(changes)
    return body


@pytest.mark.parametrize(
    "make, code",
    [
        (lambda d: ["not", "a", "dict"], "bad_request"),
        (lambda d: _bad(d, id="../../etc/passwd"), "bad_job_id"),
        (lambda d: _bad(d, id=JOB.upper()), "bad_job_id"),
        (lambda d: _bad(d, location_id="x' or 1=1"), "bad_folder_id"),
        (lambda d: _bad(d, title=""), "bad_title"),
        (lambda d: _bad(d, title="א" * 121), "bad_title"),
        (lambda d: _bad(d, title=["list"]), "bad_title"),
        (lambda d: _bad(d, title="🏠🌳"), "title_not_drawable"),
        (lambda d: _bad(d, images=[]), "bad_images"),
        (lambda d: _bad(d, images=[{"id": f"img{i:06d}", "name": "a.jpg"} for i in range(13)]), "bad_images"),
        (lambda d: _bad(d, images=[{"id": d.images[0], "name": "a.jpg"}] * 2), "bad_images"),
        (lambda d: _bad(d, images=[{"id": d.images[0], "name": "א" * 301}]), "bad_images"),
        (lambda d: _bad(d, images=[{"id": d.images[0]}]), "bad_images"),
        (lambda d: _bad(d, images=["id"]), "bad_images"),
    ],
)
def test_input_validation(app, logged_in, drive, make, code):
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=make(drive))
    assert resp.status_code == 400 and resp.get_json() == {"error": code}
    assert not (app.extensions["images2pdf"].settings.data_dir / "jobs").exists() or not any(
        (app.extensions["images2pdf"].settings.data_dir / "jobs").glob("*.json")
    )


@pytest.mark.parametrize("path", ["/api/pdf/jobs/NOTHEX", "/api/pdf/jobs/NOTHEX/file", "/api/pdf/jobs/..%2F..%2Fsecret"])
def test_job_paths_accept_only_job_ids(logged_in, path):
    assert logged_in.get(f"{BASE_URL}{path}").status_code in (400, 404)


def test_unknown_job_is_404(logged_in, drive):
    assert logged_in.get(f"{BASE_URL}/api/pdf/jobs/{_job_id(99)}").status_code == 404
    assert logged_in.get(f"{BASE_URL}/api/pdf/jobs/{_job_id(99)}/file").status_code == 404


def test_api_requires_login(client, drive):
    assert client.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive)).status_code == 401
    assert client.get(f"{BASE_URL}/api/pdf/jobs/{JOB}").status_code == 401


def test_cross_origin_post_is_rejected(logged_in, drive):
    resp = logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive), headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert _pdfs_in_drive(drive) == []


def test_untrusted_drive_link_is_not_passed_to_the_browser():
    assert pdf_jobs._drive_link("https://drive.google.com/file/d/abc/view") == "https://drive.google.com/file/d/abc/view"
    for bad in ("http://drive.google.com/x", "https://drive.google.com.evil.net/x", "https://evil.example/x",
                "https://drive.google.com:8443/x", "https://user@drive.google.com/x", None, 5):
        assert pdf_jobs._drive_link(bad) is None


def test_no_file_paths_or_ids_in_logs(app, logged_in, drive, caplog):
    caplog.set_level("INFO")
    logged_in.post(f"{BASE_URL}/api/pdf/jobs", json=_body(drive))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "וילה" not in text and "סלון" not in text and drive.location not in text
    assert os.fspath(app.extensions["images2pdf"].settings.data_dir) not in text
