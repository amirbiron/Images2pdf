"""ניתוק הדפדפן באמצע יצירה (מסך שננעל, מעבר לאפליקציה אחרת), מול gunicorn אמיתי.

הטענה ב-pdf_jobs.py: gunicorn לא עוצר את ה-view כשהלקוח מתנתק, ולכן העבודה מסתיימת
כולל השמירה בדרייב, והדפדפן שחוזר מקבל את המצב בלי ליצור קובץ שני. כאן מנתקים את
החיבור באמצע עבודה ובודקים בדיוק את זה, עם gunicorn.conf.py של הפרויקט (gthread, worker
אחד, 4 threads).
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from helpers import CHOSEN_PASSWORD

ROOT = Path(__file__).resolve().parent.parent
GUNICORN = shutil.which("gunicorn", path=str(Path(sys.executable).parent)) or shutil.which("gunicorn")
JOB = "feedfacecafebeef0123456789abcdef"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


@pytest.mark.slow
def test_job_finishes_and_saves_to_drive_after_the_client_disconnects(tmp_path):
    if GUNICORN is None:
        pytest.skip("gunicorn is not installed in this environment")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "DATA_DIR": str(data_dir), "JOB_APP_DOWNLOAD_DELAY": "0.5"}
    proc = subprocess.Popen(
        [GUNICORN, "-c", str(ROOT / "gunicorn.conf.py"), "--bind", f"127.0.0.1:{port}",
         "--chdir", str(ROOT / "tests"), "job_app:app"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    try:
        deadline = time.time() + 20
        while True:
            try:
                status, _, raw = _request(port, "GET", "/_test/ids")
                if status == 200:
                    ids = json.loads(raw)
                    break
            except OSError:
                pass
            if time.time() > deadline:
                raise AssertionError("gunicorn did not start")
            time.sleep(0.2)

        # כניסה (ב-production העוגייה Secure; כאן שולחים אותה ידנית ב-HTTP)
        status, headers, _ = _request(
            port, "POST", "/login", body=urlencode({"password": CHOSEN_PASSWORD}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == 303
        cookie = headers["Set-Cookie"].split(";")[0]
        base_headers = {"Cookie": cookie, "Origin": "https://images2pdf.test"}

        body = json.dumps({
            "id": JOB, "location_id": ids["location"], "title": "וילה כרמל",
            "images": [{"id": i, "name": f"{n}.jpg"} for n, i in enumerate(ids["images"])],
        }).encode()
        raw_request = (
            f"POST /api/pdf/jobs HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\n"
            f"Origin: https://images2pdf.test\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall(raw_request)
        time.sleep(0.8)  # באמצע: 4 תמונות × 0.5 שניות
        sock.close()  # הדפדפן "נעלם"

        status, _, raw = _request(port, "GET", f"/api/pdf/jobs/{JOB}", headers=base_headers)
        assert status == 202 and json.loads(raw)["status"] == "running", raw

        deadline = time.time() + 30
        while True:
            status, _, raw = _request(port, "GET", f"/api/pdf/jobs/{JOB}", headers=base_headers)
            job = json.loads(raw)
            if job["status"] != "running" or time.time() > deadline:
                break
            time.sleep(0.3)
        assert job["status"] == "done", job
        assert job["saved_to_drive"] is True and job["drive_url"].startswith("https://drive.google.com/")

        # "חזרה לאפליקציה": ניסיון חוזר עם אותו מזהה לא יוצר קובץ שני
        status, _, raw = _request(port, "POST", "/api/pdf/jobs", body=body,
                                  headers={**base_headers, "Content-Type": "application/json"})
        assert status == 200 and json.loads(raw)["id"] == JOB
        status, _, raw = _request(port, "GET", f"/api/pdf/jobs/{JOB}/file", headers=base_headers)
        assert status == 200 and raw.startswith(b"%PDF")
        assert len(json.loads(_request(port, "GET", "/_test/pdfs")[2])) == 1
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        print(out.decode(errors="replace")[-3000:])
