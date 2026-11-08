"""קבועים ועזרים לבדיקות (מותר לייבא; fixtures — לא)."""

from __future__ import annotations

from pathlib import Path

BASE_URL = "https://images2pdf.test"
PASSWORD = "סיסמה-בעברית-123"


def production_env(data_dir: Path, **overrides: str) -> dict[str, str]:
    """ההגדרות שהייצור רץ בהן (APP_ENV=production), עם ערכי בדיקה."""
    env = {
        "APP_ENV": "production",
        "DATA_DIR": str(data_dir),
        "WEB_PANEL_SECRET": PASSWORD,
        "TOKEN_ENCRYPTION_KEY": "k" * 44,
        "GOOGLE_CLIENT_ID": "1234-abc.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
        "ALLOWED_GOOGLE_EMAIL": "owner@example.com",
        "RENDER_EXTERNAL_URL": BASE_URL,
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


# הסיסמה שהמשתמש "בחר" אחרי הכניסה החד-פעמית עם PASSWORD (שלב 4). בעברית, כמו בייצור.
CHOSEN_PASSWORD = "הסיסמה-שבחרתי-42"


def seed_password(app, password: str = CHOSEN_PASSWORD):
    """מצב אחרי שהמשתמש בחר סיסמה: PASSWORD (מ-WEB_PANEL_SECRET) כבר לא עובדת."""
    store = app.extensions["images2pdf"].password_store
    if store.current() is None:
        store.set_initial(password)
    return store.current()


def sign_in(app, client, password: str = CHOSEN_PASSWORD):
    """כניסה רגילה דרך /login, עם סיסמה שכבר נבחרה. מחזיר את הלקוח."""
    seed_password(app)
    resp = client.post(f"{BASE_URL}/login", data={"password": password, "next": "/"})
    assert resp.status_code == 303, resp.status_code
    return client
