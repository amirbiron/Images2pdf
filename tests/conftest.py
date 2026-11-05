"""Fixtures משותפים. pytest מוצא אותם לבד — לא מייבאים אותם בקבצי הבדיקה."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings  # noqa: E402
from helpers import BASE_URL, PASSWORD, production_env  # noqa: E402
from web_app import create_app  # noqa: E402

@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def make_app(data_dir: Path):
    def _make(**overrides: str):
        app = create_app(load_settings(production_env(data_dir, **overrides)))
        app.config["TESTING"] = True
        return app

    return _make


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(app):
    c = app.test_client()
    resp = c.post(f"{BASE_URL}/login", data={"password": PASSWORD, "next": "/"})
    assert resp.status_code == 303
    return c
