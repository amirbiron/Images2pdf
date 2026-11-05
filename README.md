# כלי PDF ללוקיישני צילום

בוחרים עד 12 תמונות מתיקיית לוקיישן ב-Google Drive ומקבלים PDF לשליחה. התוכנית המלאה ב-[`ROADMAP.md`](ROADMAP.md).

- הקמה של שלב 0 (Google Cloud, Render, דף הבדיקות): [`docs/setup-stage0.md`](docs/setup-stage0.md)
- מנוע ה-PDF (שלב 1): `pdf_layout.py` (פריסה, חישוב בלבד), `image_processing.py` (עיבוד תמונה אחת), `pdf_builder.py` (כתיבת הקובץ), ו-`pdf_job.py` שמחבר אותם. הדף `/dev/sample-pdf` יוצר קובץ מתיקייה ומציג זמן, זיכרון ומשקל.
- הפונט Heebo נמצא ב-`assets/fonts`, ברישיון SIL Open Font License (`assets/fonts/OFL.txt`).
- בדיקות: `pip install -r requirements-dev.txt` ואז `pytest`. בדיקה בדפדפן: `node tests/browser/login_and_checks.mjs <נתיב ל-gunicorn>`.
