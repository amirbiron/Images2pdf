"""דף הפרטיות הציבורי, דף העזרה, התפריט, וקיצור הדרך למסך הבית (שלב 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import BASE_URL, production_env
from PIL import Image

from config import load_settings
from web_app import create_app

ROOT_STATIC = Path(__file__).resolve().parent.parent / "static"


def test_privacy_page_is_public_and_bilingual(client):
    resp = client.get(f"{BASE_URL}/privacy")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "מדיניות פרטיות" in body and "Privacy Policy" in body
    for fact in ("drive.readonly", "drive.file", "24 שעות", "Render", "myaccount.google.com/linkedapps"):
        assert fact in body, fact


@pytest.mark.parametrize("path", ["/privacy/", "/privacy/x", "/privacyx", "/privacy.html"])
def test_only_the_exact_privacy_path_is_public(client, path):
    resp = client.get(f"{BASE_URL}{path}")
    assert resp.status_code in (302, 404)
    assert "Privacy Policy" not in resp.get_data(as_text=True)


def test_a_route_that_merely_starts_with_privacy_still_needs_login(make_app):
    """גם נתיב קיים שמתחיל ב-/privacy (כאן: נתיב בדיקה) לא נפתח בלי כניסה. ציבורי רק /privacy עצמו."""
    app = make_app()
    app.add_url_rule("/privacy-extra", "privacy_extra", lambda: "secret page")
    app.add_url_rule("/privacy/details", "privacy_details", lambda: "secret page")
    c = app.test_client()
    for path in ("/privacy-extra", "/privacy/details"):
        resp = c.get(f"{BASE_URL}{path}")
        assert resp.status_code == 302 and "secret page" not in resp.get_data(as_text=True), path


def test_privacy_contact_comes_from_the_environment(make_app):
    with_contact = make_app(PRIVACY_CONTACT_EMAIL="privacy@example.org").test_client()
    assert "mailto:privacy@example.org" in with_contact.get(f"{BASE_URL}/privacy").get_data(as_text=True)
    without = make_app().test_client().get(f"{BASE_URL}/privacy").get_data(as_text=True)
    assert "mailto:" not in without and "מי שהקים את הכלי" in without
    bad = make_app(PRIVACY_CONTACT_EMAIL="not an email").test_client().get(f"{BASE_URL}/privacy").get_data(as_text=True)
    assert "mailto:" not in bad


def test_privacy_page_works_even_when_the_service_is_not_configured(data_dir):
    app = create_app(load_settings(production_env(data_dir, WEB_PANEL_SECRET=None)))
    assert app.test_client().get(f"{BASE_URL}/privacy").status_code == 200


def test_help_page_covers_the_handoff_topics(logged_in):
    body = logged_in.get(f"{BASE_URL}/help").get_data(as_text=True)
    for topic in ("תחילת מילה", "12 תמונות", "שיתוף", "הורדה", "קבצי PDF", "/password", "לא מאומתת",
                  "Open as Web App", "הוספה למסך הבית"):
        assert topic in body, topic


def test_menu_has_help_and_password_and_no_dev_pages(logged_in):
    page = logged_in.get(f"{BASE_URL}/settings").get_data(as_text=True)
    assert 'href="/help"' in page and 'href="/password"' in page
    assert "/dev/" not in page


def test_home_screen_shortcut_opens_in_the_browser(client):
    page = client.get(f"{BASE_URL}/login").get_data(as_text=True)
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page
    assert 'rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png"' in page
    resp = client.get(f"{BASE_URL}/static/manifest.webmanifest")  # ציבורי: הדפדפן מבקש אותו בלי עוגייה
    assert resp.status_code == 200 and resp.mimetype == "application/manifest+json"
    manifest = json.loads(resp.data)
    assert manifest["display"] == "browser" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        w, h = map(int, icon["sizes"].split("x"))
        with Image.open(ROOT_STATIC / icon["src"]) as im:
            assert im.size == (w, h)
    with Image.open(ROOT_STATIC / "icons" / "apple-touch-icon.png") as im:
        assert im.size == (180, 180) and im.mode == "RGB"  # בלי שקיפות: iOS ממלא שקיפות בשחור
