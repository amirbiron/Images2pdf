"""
gunicorn.conf.py — נטען אוטומטית מתיקיית העבודה (WORKDIR=/app).
מקור: gunicorn/config.py, get_default_config_file ("./gunicorn.conf.py").
"""

import os

_port = os.environ.get("PORT", "10000").strip()
if not _port.isdigit() or not 0 < int(_port) < 65536:
    print(f"gunicorn.conf.py: invalid PORT {_port!r}, using 10000", flush=True)
    _port = "10000"
# Render מצפה לשרת על 0.0.0.0, ו-PORT ברירת המחדל שלו הוא 10000.
# מקור: https://render.com/docs/web-services#port-binding
bind = f"0.0.0.0:{_port}"

# worker אחד עם threads (כמו בפאנל המקורי): העבודה מחכה בעיקר לגוגל, ובמגבלת
# 512MB תהליך אחד חוסך זיכרון. threads > 1 מפעיל את ה-worker מסוג gthread.
workers = 1
threads = 4

# מחזור ה-worker מחזיר זיכרון למערכת (Pillow ו-googleapiclient מפצלים את ה-heap).
max_requests = 250
max_requests_jitter = 50

# יצירת PDF מוערכת בעד דקה, ו-ROADMAP קובע לחשוב מחדש מעל שתי דקות.
# timeout הוא הערך שקובע אם מחזור max_requests קוטע יצירה באמצע: אחרי המחזור ה-worker
# יוצא מהלולאה שמדווחת "אני חי" ל-arbiter, וה-arbiter הורג אותו אחרי timeout שניות.
# graceful_timeout לבדו לא קוטע במחזור כזה (פייתון מחכה ל-threads לפני היציאה), אבל
# הוא קובע בעצירה עם SIGTERM, ולכן גם הוא מכסה את זמן היצירה. ב-Render עם דיסק קבוע
# זמן ההמתנה בעצירה קבוע ל-30 שניות ואי אפשר לשנות אותו, כך שפריסה חדשה באמצע יצירה
# תקטע אותה. מקור: gunicorn/workers/gthread.py run(), gunicorn/arbiter.py murder_workers
# ו-spawn_worker, https://render.com/docs/disks. נמדד ב-tests/test_gunicorn_restart.py.
graceful_timeout = 300
timeout = 300

# בלי access log: כתובת ה-callback של OAuth כוללת code ו-state בשורת הבקשה.
accesslog = None
