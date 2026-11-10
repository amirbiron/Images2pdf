# כלי PDF ללוקיישני צילום

בוחרים עד 12 תמונות מתיקיית לוקיישן ב-Google Drive ומקבלים PDF לשליחה. התוכנית המלאה ב-[`ROADMAP.md`](ROADMAP.md).

- הקמה של שלב 0 (Google Cloud, Render): [`docs/setup-stage0.md`](docs/setup-stage0.md)
- מסירה ללקוח (שלב 4), כולל איפוס סיסמה והעברת Render: [`docs/handoff.md`](docs/handoff.md)
- כניסה (שלב 4): `password_store.py`. הסיסמה ב-`WEB_PANEL_SECRET` חד-פעמית, ואחריה בוחרים סיסמה שנשמרת בדיסק כ-hash. החלפת `WEB_PANEL_SECRET` ב-Render מאפסת אותה. דף העזרה ב-`/help`, ודף הפרטיות הציבורי ב-`/privacy` (כתובת הקשר בו מ-`PRIVACY_CONTACT_EMAIL`).
- דף הבית הציבורי ב-`/` (לאימות ה-Branding של גוגל, עם תגית Search Console מ-`GOOGLE_SITE_VERIFICATION`), והכלי עצמו ב-`/app`.
- מנוע ה-PDF (שלב 1): `pdf_layout.py` (פריסה, חישוב בלבד), `image_processing.py` (עיבוד תמונה אחת), `pdf_builder.py` (כתיבת הקובץ), ו-`pdf_job.py` שמחבר אותם. הדף `/dev/sample-pdf`, שיצר קובץ לבדיקה ומדד זמן, זיכרון ומשקל, הוסר בשלב 4.
- הבורר (שלב 2): כשהחשבון מחובר לגוגל וההגדרות תקינות, המסך הראשון של הכלי (`/app`) הוא חיפוש לוקיישן. אחרת `/app` מציג את מסך החיבור, שנמצא תמיד גם ב-`/settings`. `static/picker.js` מנהל את המסכים, הבחירה והכותרת, ו-`picker_api.py` מספק את החיפוש ואת רשימת הקבצים.
- יצירה ושמירה (שלב 3): `pdf_jobs.py`. הדפדפן שולח בקשת יצירה עם מזהה משלו, והיצירה רצה בתוך הבקשה. מצב העבודה נשמר ב-`DATA_DIR/jobs` ל-24 שעות, כדי שדפדפן שהחיבור שלו נפל (מסך נעול) יקבל את הקובץ כשהוא חוזר, בלי ליצור אותו שוב. כל קובץ נשמר בתיקייה `קבצי PDF` בדרייב, ומוצע להורדה ולשיתוף.
- הפונט Heebo נמצא ב-`assets/fonts`, ברישיון SIL Open Font License (`assets/fonts/OFL.txt`).
- בדיקות: `pip install -r requirements-dev.txt` ואז `pytest`. הבדיקות בדפדפן, בשלושה רוחבים, רצות מתוך pytest (`tests/test_picker_browser.py` לבורר, `tests/test_pages_browser.py` לכניסה, לסיסמה, לעזרה ולפרטיות). הן דורשות Node עם החבילה `playwright` ודפדפן Chromium (`npx playwright install chromium`). בלעדיהם pytest מדלג עליהן ומסמן אותן כ-skipped.
