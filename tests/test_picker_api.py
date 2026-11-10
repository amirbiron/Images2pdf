"""ה-API של הבורר מול דרייב מזויף: חיפוש עם שם האב, רשימת קבצים בעמודים, ולידציה."""

from __future__ import annotations

import pytest
from fake_drive import FakeDrive
from helpers import BASE_URL

import google_api
import picker_api
from token_store import StoredGrant


@pytest.fixture
def drive(app, monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(google_api, "get_drive_service", lambda: fake)
    root = fake.add("לוקיישנים", owned=True, shared=False)
    villa = fake.add("וילה כרמל", parent=root)
    fake.add("חדרי אמבטיה", parent=villa)
    fake.add("גינה", parent=villa)
    zimmer = fake.add("צימר ג'ורג' בגליל", parent=root)
    fake.add("חדרי אמבטיה", parent=zimmer)
    for i in (1, 2, 12, 3):
        fake.add(f"IMG_{i}.jpg", "image/jpeg", villa)
    fake.add("סרטון.mp4", "video/mp4", villa)
    fake.add("מסמך.pdf", "application/pdf", villa)
    fake.ids = {"root": root, "villa": villa, "zimmer": zimmer}
    app.extensions["images2pdf"].token_store.save(StoredGrant("1//r", "owner@example.com", ("a",), "2026-11-06T00:00:00+00:00"))
    return fake


def _get(client, path):
    resp = client.get(f"{BASE_URL}{path}")
    return resp.status_code, resp.get_json()


@pytest.mark.parametrize("q", ["ג'ור", "כרמ", "צימר"])
def test_search_finds_word_prefixes_including_second_word_and_geresh(logged_in, drive, q):
    status, data = _get(logged_in, f"/api/drive/search?q={q}")
    assert status == 200
    names = [r["name"] for r in data["results"]]
    assert any(q in n for n in names), names


def test_search_results_carry_parent_name_from_one_batch(logged_in, drive):
    status, data = _get(logged_in, "/api/drive/search?q=חדרי")
    assert status == 200
    parents = sorted(r["parent"]["name"] for r in data["results"])
    assert parents == ["וילה כרמל", "צימר ג'ורג' בגליל"]  # שני "חדרי אמבטיה", כל אחד עם האב שלו
    assert drive.batches == [2]  # בקשת batch אחת לשני האבות, לא קריאה לכל תוצאה


def test_parent_that_the_account_cannot_see_is_simply_missing(logged_in, drive):
    del drive.files_by_id[drive.ids["root"]]  # האב של "וילה כרמל" לא נגיש
    status, data = _get(logged_in, "/api/drive/search?q=וילה")
    assert status == 200 and data["results"][0]["parent"] is None


@pytest.mark.parametrize("status, reason", [(429, "rateLimitExceeded"), (403, "userRateLimitExceeded"), (500, "backendError")])
def test_parent_lookup_failure_is_an_error_not_a_hidden_parent(logged_in, drive, status, reason):
    """רק 404 אומר "החשבון לא רואה את התיקייה". מגבלת קצב או תקלה אצל גוגל לא יוצגו
    כתוצאות בלי שם האב, כי אז שני "חדרי אמבטיה" נראים זהים בלי שום סימן לתקלה."""
    drive.get_errors[drive.ids["root"]] = (status, reason)
    resp = logged_in.get(f"{BASE_URL}/api/drive/search?q=וילה")
    assert resp.status_code in (403, 502) and resp.get_json()["error"] in ("forbidden", "drive_error")


def test_search_is_naturally_ordered_and_escaped(logged_in, drive):
    status, _ = _get(logged_in, "/api/drive/search?q=a'b\\c")
    assert status == 200
    query = drive.queries[-1]
    assert "name contains 'a\\'b\\\\c'" in query


@pytest.mark.parametrize(
    "path, code",
    [
        ("/api/drive/search", "query_missing"),
        ("/api/drive/search?q=%20%20", "query_missing"),
        ("/api/drive/search?q=" + "א" * 101, "query_too_long"),
        ("/api/drive/search?q=ok&page_token=" + "x" * 2001, "bad_page_token"),
        ("/api/drive/search?q=ok&page_token=%0Abad", "bad_page_token"),
        ("/api/drive/files", "bad_folder_id"),
        ("/api/drive/files?folder_id=x'%20or%201=1", "bad_folder_id"),
    ],
)
def test_input_validation(logged_in, drive, path, code):
    status, data = _get(logged_in, path)
    assert status == 400 and data == {"error": code}


def test_files_returns_folders_and_jpeg_png_only_in_natural_order(logged_in, drive):
    status, data = _get(logged_in, f"/api/drive/files?folder_id={drive.ids['villa']}")
    assert status == 200
    assert [f["name"] for f in data["folders"]] == ["גינה", "חדרי אמבטיה"]
    assert [i["name"] for i in data["images"]] == ["IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "IMG_12.jpg"]
    assert all(i["thumbnailLink"].startswith("https://lh3.googleusercontent.com/") for i in data["images"])


def test_files_pages_through_large_folders(logged_in, drive, monkeypatch):
    """תיקייה עם יותר מ-50 תמונות: הפאנל המקורי הציג רק את העמוד הראשון."""
    big = drive.add("גינה גדולה", parent=drive.ids["villa"])
    for i in range(130):
        drive.add(f"P{i:03d}.jpg", "image/jpeg", big)
    monkeypatch.setattr(picker_api, "LIST_PAGE_SIZE", 50)
    seen, token, pages = [], None, 0
    while True:
        path = f"/api/drive/files?folder_id={big}" + (f"&page_token={token}" if token else "")
        status, data = _get(logged_in, path)
        assert status == 200
        seen += [i["name"] for i in data["images"]]
        pages += 1
        token = data["nextPageToken"]
        if not token:
            break
    assert pages == 3 and len(seen) == 130 and len(set(seen)) == 130


def test_untrusted_thumbnail_host_is_not_passed_to_the_browser(logged_in, drive):
    img = next(f for f in drive.files_by_id.values() if f["name"] == "IMG_1.jpg")
    img["thumbnailLink"] = "https://evil.example/x.jpg"
    _, data = _get(logged_in, f"/api/drive/files?folder_id={drive.ids['villa']}")
    first = next(i for i in data["images"] if i["name"] == "IMG_1.jpg")
    assert first["thumbnailLink"] is None  # הדפדפן ילך ישר לנתיב שדרך השרת


def test_folder_name_lookup(logged_in, drive):
    status, data = _get(logged_in, f"/api/drive/folder/{drive.ids['zimmer']}")
    assert status == 200 and data == {"id": drive.ids["zimmer"], "name": "צימר ג'ורג' בגליל"}
    assert _get(logged_in, "/api/drive/folder/doesNotExist1")[0] == 404
    image = next(i for i, f in drive.files_by_id.items() if f["mimeType"] == "image/jpeg")
    assert _get(logged_in, f"/api/drive/folder/{image}")[0] == 404


def test_api_requires_login(client, drive):
    status, data = _get(client, "/api/drive/search?q=וילה")
    assert status == 401 and data == {"error": "unauthorized"}


def test_home_is_the_search_screen_when_connected(logged_in, drive):
    page = logged_in.get(f"{BASE_URL}/app").get_data(as_text=True)
    assert 'id="search-input"' in page and "picker.js" in page


def test_home_is_the_connection_page_when_not_connected(logged_in):
    page = logged_in.get(f"{BASE_URL}/app").get_data(as_text=True)
    assert 'id="search-input"' not in page
    assert "חיבור לגוגל" in page
