# כלי PDF ללוקיישני צילום

בוחרים עד 12 תמונות מתיקיית לוקיישן ב-Google Drive ומקבלים PDF לשליחה. התוכנית המלאה ב-[`ROADMAP.md`](ROADMAP.md).

- הקמה של שלב 0 (Google Cloud, Render, דף הבדיקות): [`docs/setup-stage0.md`](docs/setup-stage0.md)
- מנוע ה-PDF (שלב 1): `pdf_layout.py` (פריסה, חישוב בלבד), `image_processing.py` (עיבוד תמונה אחת), `pdf_builder.py` (כתיבת הקובץ), ו-`pdf_job.py` שמחבר אותם. הדף `/dev/sample-pdf` יוצר קובץ מתיקייה ומציג זמן, זיכרון ומשקל.
- הבורר (שלב 2): כשהחשבון מחובר לגוגל וההגדרות תקינות, המסך הראשון (`/`) הוא חיפוש לוקיישן. אחרת `/` מציג את מסך החיבור, שנמצא תמיד גם ב-`/settings`. `static/picker.js` מנהל את המסכים, הבחירה והכותרת, ו-`picker_api.py` מספק את החיפוש ואת רשימת הקבצים.
- יצירה ושמירה (שלב 3): `pdf_jobs.py`. הדפדפן שולח בקשת יצירה עם מזהה משלו, והיצירה רצה בתוך הבקשה. מצב העבודה נשמר ב-`DATA_DIR/jobs` ל-24 שעות, כדי שדפדפן שהחיבור שלו נפל (מסך נעול) יקבל את הקובץ כשהוא חוזר, בלי ליצור אותו שוב. כל קובץ נשמר בתיקייה `קבצי PDF` בדרייב, ומוצע להורדה ולשיתוף.
- הפונט Heebo נמצא ב-`assets/fonts`, ברישיון SIL Open Font License (`assets/fonts/OFL.txt`).
- בדיקות: `pip install -r requirements-dev.txt` ואז `pytest`. בדיקה בדפדפן: `node tests/browser/login_and_checks.mjs <נתיב ל-gunicorn>`. הבדיקה של הבורר בשלושה רוחבים רצה מתוך pytest (`tests/test_picker_browser.py`), ודורשת Node עם החבילה `playwright` ודפדפן Chromium (`npx playwright install chromium`). בלעדיהם pytest מדלג עליה ומסמן אותה כ-skipped.
