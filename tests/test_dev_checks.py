"""הדף /dev/checks מקצה לקצה מול דרייב מזויף (tests/fake_drive.py)."""

from __future__ import annotations

import json

import pytest
from fake_drive import FakeDrive
from helpers import BASE_URL

import dev_checks
import google_api


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(google_api, "get_drive_service", lambda: fake)
    monkeypatch.setattr(dev_checks, "get_drive_service", lambda: fake)
    sample = fake.add("וילה בכרמל")
    fake.add("חדרי אמבטיה", parent=sample)
    fake.add("ג'ורג' סטודיו", parent=sample)
    for i in range(3):
        fake.add(f"תמונה {i}.jpg", "image/jpeg", sample)
    fake.add("סרטון.mp4", "video/mp4", sample)
    fake.add("מסמך שלי.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             owned=True, shared=False, can_edit=True)
    fake.sample_id = sample
    return fake


def _run(client, folder_id) -> dict:
    resp = client.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": folder_id})
    assert resp.status_code == 303, resp.get_data(as_text=True)[:500]
    return {c["num"]: c for c in _last(client)["checks"]}


def _last(client) -> dict:
    app = client.application
    path = app.extensions["images2pdf"].settings.data_dir / "dev_checks_last.json"
    return json.loads(path.read_text())


def _texts(check) -> str:
    return "\n".join(f"{line['status']}: {line['text']}" for line in check["lines"])


def test_all_checks_pass_against_documented_drive_behaviour(logged_in, drive):
    checks = _run(logged_in, drive.sample_id)
    assert {n: c["status"] for n, c in checks.items()} == {1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "info"}, {
        n: _texts(c) for n, c in checks.items()
    }
    assert "שותפה עם החשבון" in _texts(checks[1])
    assert "וילה בכרמל" in _texts(checks[1])  # שם תיקיית האב בתוצאה
    # אמצע מילה לא נמצא בחיפוש קידומת — מסומן כמידע, לא כישלון
    assert any(line["status"] == "info" and "אמצע מילה" in line["text"] and "לא נמצאה" in line["text"] for line in checks[2]["lines"])
    # הסרטון לא נכנס לבדיקת התמונות
    assert [t["name"] for t in _last(logged_in)["thumbnails"]] == ["תמונה 0.jpg", "תמונה 1.jpg", "תמונה 2.jpg"]
    # ניסיון השינוי היה על תמונה של הלקוח ועל קובץ שהחשבון יכול לערוך
    names = [drive.files_by_id[i]["name"] for i in drive.update_attempts]
    assert names == ["תמונה 0.jpg", "מסמך שלי.docx"]

    page = logged_in.get(f"{BASE_URL}/dev/checks").get_data(as_text=True)
    assert page.count("data-thumb-id=") == 3
    assert "https://lh3.googleusercontent.com/drive-storage/" in page


def test_second_run_reuses_folders_and_cleans_previous_upload(logged_in, drive):
    _run(logged_in, drive.sample_id)
    folders_before = {f["name"] for f in drive.files_by_id.values() if f["by_app"] and f["mimeType"] == "application/vnd.google-apps.folder"}
    uploads_before = [f for f in drive.files_by_id.values() if f["by_app"] and f["name"].endswith(".txt")]
    checks = _run(logged_in, drive.sample_id)
    uploads_after = [f for f in drive.files_by_id.values() if f["by_app"] and f["name"].endswith(".txt")]
    assert folders_before == {"בדיקות כלי PDF", "צימר ג'ורג' בגליל"}
    assert len([f for f in drive.files_by_id.values() if f["name"] == "בדיקות כלי PDF"]) == 1
    assert len(uploads_before) == 1 and len(uploads_after) == 1
    assert uploads_before[0]["id"] != uploads_after[0]["id"]
    assert "נמחק" in _texts(checks[3])
    # בריצה השנייה תיקיית הגרש כבר קיימת, ולכן החיפוש שלה הוא חלק מהתנאי לעבור
    assert checks[2]["status"] == "pass"


def test_check_4_fails_loudly_if_writes_are_not_blocked(logged_in, drive, monkeypatch):
    original_update = drive.update

    def permissive_update(fileId=None, body=None, **kw):
        drive.files_by_id[fileId]["by_app"] = True  # מדמה הרשאה רחבה מדי
        return original_update(fileId=fileId, body=body, **kw)

    monkeypatch.setattr(drive, "update", permissive_update)
    checks = _run(logged_in, drive.sample_id)
    assert checks[4]["status"] == "fail"
    assert "עוצרים ומדווחים" in _texts(checks[4])


def test_check_4_warns_when_nothing_editable_exists(logged_in, drive):
    for f in drive.files_by_id.values():
        f["capabilities"]["canEdit"] = False
    checks = _run(logged_in, drive.sample_id)
    assert checks[4]["status"] == "warn"


def test_owned_sample_folder_is_flagged(logged_in, drive):
    drive.files_by_id[drive.sample_id]["ownedByMe"] = True
    checks = _run(logged_in, drive.sample_id)
    assert checks[1]["status"] == "warn"


def test_invisible_folder_is_a_clear_fatal_message(logged_in, drive):
    _run_resp = logged_in.post(f"{BASE_URL}/dev/checks/run", data={"folder_id": "doesNotExist123"})
    assert _run_resp.status_code == 303
    assert _last(logged_in)["fatal"] == "החשבון שחובר לא רואה את תיקיית הדוגמה."


def test_a_crashing_check_does_not_hide_the_others(logged_in, drive, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(dev_checks, "check_1_search_shared", boom)
    checks = _run(logged_in, drive.sample_id)
    assert checks[1]["status"] == "fail"
    assert checks[3]["status"] == "pass"
