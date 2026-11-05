"""מחזור worker אחרי max_requests לא קוטע בקשה ארוכה באמצע (ROADMAP: "לבדוק את זה בפועל").

מריצים gunicorn אמיתי עם gunicorn.conf.py של הפרויקט, ומקטינים מהשורה רק את
הערכים שקובעים את התזמון (CLI גובר על קובץ ההגדרות). בקשה של 6 שניות רצה, ובזמן
הזה שתי בקשות קצרות מביאות את ה-worker ל-max_requests.

מה נמדד (gunicorn 26.2, worker מסוג gthread):
- timeout קצר הורג את הבקשה, גם כש-graceful_timeout ארוך: אחרי max_requests
  ה-worker יוצא מהלולאה שמדווחת "אני חי", וה-arbiter הורג אותו אחרי timeout שניות.
- graceful_timeout קצר לא הורג את הבקשה במחזור כזה: ה-worker קורא ל-sys.exit(0),
  ופייתון מחכה ל-threads של ThreadPoolExecutor לפני שהתהליך נגמר.
  מקור: gunicorn/arbiter.py (spawn_worker: init_process ואז sys.exit(0)),
  concurrent/futures/thread.py (_python_exit מצטרף לכל ה-threads).
לכן הערך שקובע במחזור הוא timeout. graceful_timeout קובע בעצירה עם SIGTERM.
"""

from __future__ import annotations

import os
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUNICORN = shutil.which("gunicorn", path=str(Path(sys.executable).parent)) or shutil.which("gunicorn")
SLOW_SECONDS = 6


def test_production_values_cover_pdf_generation():
    conf = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert conf["workers"] == 1 and conf["threads"] == 4
    # יצירת PDF: עד שתי דקות לפני שחושבים מחדש (ROADMAP). שני הערכים צריכים לכסות אותה.
    assert conf["graceful_timeout"] >= 180
    assert conf["timeout"] >= conf["graceful_timeout"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, timeout: float) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return f"error:{type(exc).__name__}"


def _run_scenario(timeout: int, graceful: int) -> str:
    if GUNICORN is None:
        pytest.skip("gunicorn is not installed in this environment")
    port = _free_port()
    proc = subprocess.Popen(
        [
            GUNICORN, "-c", str(ROOT / "gunicorn.conf.py"),
            "--bind", f"127.0.0.1:{port}",
            "--max-requests", "2", "--max-requests-jitter", "0",
            "--timeout", str(timeout), "--graceful-timeout", str(graceful),
            "--chdir", str(ROOT / "tests"), "slow_app:app",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "PORT": str(port)},
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 15
        while _get(base + "/ready", 1) != "fast":
            if time.time() > deadline:
                raise AssertionError("gunicorn did not start")
            time.sleep(0.2)
        # /ready נספר כבקשה 1; ה-worker הבא אחרי המחזור הזה נקי.
        while _get(base + "/ready", 1) != "fast":
            time.sleep(0.2)
        time.sleep(1.5)  # מחכים שה-worker הראשון יוחלף אחרי 2 בקשות

        result: dict[str, str] = {}
        t = threading.Thread(target=lambda: result.setdefault("slow", _get(f"{base}/slow?{SLOW_SECONDS}", 30)))
        t.start()
        time.sleep(0.5)
        _get(base + "/fast", 5)  # בקשה 2 → max_requests: ה-worker מפסיק לקבל ומחכה לבקשה האיטית
        t.join(40)
        return result.get("slow", "error:no-result")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.slow
def test_long_request_survives_recycle_when_both_timeouts_are_long():
    assert _run_scenario(timeout=30, graceful=30) == "slow-done"


@pytest.mark.slow
def test_short_timeout_kills_the_request_even_with_long_graceful_timeout():
    assert _run_scenario(timeout=2, graceful=30).startswith("error:")


@pytest.mark.slow
def test_short_graceful_timeout_alone_does_not_cut_the_request():
    # אם גרסה עתידית של gunicorn תתחיל לקטוע כאן, הבדיקה תיכשל — ואז graceful_timeout=300
    # שכבר מוגדר ב-gunicorn.conf.py הוא מה שיגן על יצירת הקובץ.
    assert _run_scenario(timeout=30, graceful=2) == "slow-done"
