"""שיא הזיכרון עם 12 תמונות כבדות נשאר מתחת ל-512MB (ROADMAP, שלב 1).

12 תמונות שונות (רעש אקראי, כדי שלא יידחסו לכלום ושלא יישמרו פעם אחת) של 45
מגה-פיקסל — כמו המדידה הסינתטית ב-ROADMAP. היצירה רצה בתהליך חדש שמייבא גם את
האפליקציה כולה (Flask, googleapiclient, ReportLab), כדי שבסיס הזיכרון יהיה כמו
ב-worker אמיתי. השיא נמדד ב-VmHWM של התהליך החדש (ru_maxrss של תהליך בן יורש את
ה-RSS של האב ברגע ה-fork, ולכן אינו אמין כאן).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

_RUN = r"""
import json, shutil, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import web_app  # noqa: F401 — בסיס זיכרון כמו ב-worker
from pdf_job import SourceItem, run_pdf_job
src = Path(sys.argv[2]); work = Path(sys.argv[3])
items = [SourceItem(str(i), p.name) for i, p in enumerate(sorted(src.glob("*.jpg")))]
def fetch(item, dest):
    shutil.copyfile(src / item.name, dest)
    return dest.stat().st_size
r = run_pdf_job(items, fetch, title="וילה כבדה 12", link_url=None, out_path=work / "out.pdf", work_dir=work / "w")
hwm = int(next(l for l in open("/proc/self/status") if l.startswith("VmHWM")).split()[1]) * 1024
print(json.dumps({
    "ok": r.ok, "processed": r.processed, "failed": r.failed,
    "hwm_mb": round(hwm / 2**20, 1), "sampled_peak_mb": round(r.memory.rss_peak / 2**20, 1),
    "start_mb": round(r.memory.rss_start / 2**20, 1),
    "pdf_kb": round(r.pdf_bytes / 1024), "source_mb": round(r.source_bytes / 2**20, 1),
    "seconds": round(r.seconds_total, 1), "process_seconds": round(r.seconds_process, 1),
}))
"""


@pytest.mark.slow
def test_twelve_heavy_images_stay_under_512mb(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        Image.effect_noise((8192, 5464), 40 + i).convert("RGB").save(src / f"{i:02d}.jpg", quality=92)
    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.run([sys.executable, "-W", "ignore", "-c", _RUN, str(ROOT), str(src), str(work)],
                          capture_output=True, text=True, check=True, timeout=600)
    stats = json.loads(proc.stdout.strip().splitlines()[-1])
    print("\nheavy-12:", stats)
    assert stats["ok"] and stats["processed"] == 12 and not stats["failed"]
    # 512MB הם של כל ה-container; התהליך הזה הוא החלק העיקרי, והשאר (arbiter של gunicorn,
    # page cache) צריך מקום. לכן הדרישה כאן מחמירה מ-512.
    assert stats["hwm_mb"] < 300, stats
