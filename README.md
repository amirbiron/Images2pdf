# כלי PDF ללוקיישני צילום

בוחרים עד 12 תמונות מתיקיית לוקיישן ב-Google Drive ומקבלים PDF לשליחה. התוכנית המלאה ב-[`ROADMAP.md`](ROADMAP.md).

- הקמה של שלב 0 (Google Cloud, Render, דף הבדיקות): [`docs/setup-stage0.md`](docs/setup-stage0.md)
- מנוע ה-PDF (שלב 1): `pdf_layout.py` (פריסה, חישוב בלבד), `image_processing.py` (עיבוד תמונה אחת), `pdf_builder.py` (כתיבת הקובץ), ו-`pdf_job.py` שמחבר אותם. הדף `/dev/sample-pdf` יוצר קובץ מתיקייה ומציג זמן, זיכרון ומשקל.
- הבורר (שלב 2): המסך הראשון (`/`) הוא חיפוש לוקיישן. `static/picker.js` מנהל את המסכים, הבחירה והכותרת, ו-`picker_api.py` מספק את החיפוש ואת רשימת הקבצים. מצב החיבור לגוגל עבר ל-`/settings`.
- הפונט Heebo נמצא ב-`assets/fonts`, ברישיון SIL Open Font License (`assets/fonts/OFL.txt`).
- בדיקות: `pip install -r requirements-dev.txt` ואז `pytest`. בדיקה בדפדפן: `node tests/browser/login_and_checks.mjs <נתיב ל-gunicorn>`. הבדיקה של הבורר בשלושה רוחבים רצה מתוך pytest (`tests/test_picker_browser.py`).
