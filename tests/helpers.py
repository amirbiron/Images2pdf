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
