"""כניסה ראשונה, בחירת סיסמה, החלפה, עזרה ופרטיות — בדפדפן אמיתי, בטלפון, בטאבלט ובמחשב.

השרת הוא האפליקציה במצב production, ב-HTTPS עם תעודה זמנית (כמו ב-test_picker_browser).
התרחיש ב-tests/browser/pages.mjs.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from helpers import PASSWORD, production_env
from werkzeug.serving import make_server

from config import load_settings
from test_picker_browser import _node_env, _node_with_playwright
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "browser" / "pages.mjs"


@pytest.mark.slow
def test_pages_in_real_browser(tmp_path):
    node = _node_with_playwright()
    if node is None:
        pytest.skip("node with playwright is not available")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"https://127.0.0.1:{port}"
    shots = Path(os.environ.get("PAGES_SHOTS", tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    results = []
    for vp in ("phone", "tablet", "desktop"):
        data_dir = tmp_path / f"data-{vp}"  # כל גודל מתחיל מכניסה ראשונה, עם דיסק נקי
        data_dir.mkdir()
        app = create_app(load_settings(production_env(data_dir, RENDER_EXTERNAL_URL=base,
                                                      PRIVACY_CONTACT_EMAIL="privacy@example.org")))
        server = make_server("127.0.0.1", port, app, threaded=True, ssl_context="adhoc")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            args = {"base": base, "viewport": vp, "envPassword": PASSWORD, "shots": str(shots)}
            proc = subprocess.run([node, str(SCRIPT), json.dumps(args)], capture_output=True, text=True,
                                  timeout=200, env={**_node_env(), "LANG": "C.UTF-8"})
            print(proc.stdout)
            results.append((vp, proc))
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
    for vp, proc in results:
        assert proc.returncode == 0, vp + ": " + proc.stdout[-4000:] + proc.stderr[-4000:]
        assert "ALL PAGES CHECKS PASSED" in proc.stdout
