// הבורר בדפדפן אמיתי, בשלושה רוחבים. מופעל מ-tests/test_picker_browser.py, שמרים את השרת
// עם דרייב מזויף ומעביר כאן JSON עם הכתובת, העוגייה ומזהי התיקיות.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const args = JSON.parse(process.argv[2]);
const directPng = readFileSync(args.directPng);

function assert(cond, msg) { if (!cond) { throw new Error("FAILED: " + msg); } console.log("ok  - " + msg); }

const VIEWPORTS = [
  { name: "phone", options: { ...devices["iPhone 13"], locale: "he-IL", ignoreHTTPSErrors: true } },
  { name: "tablet", options: { viewport: { width: 800, height: 1280 }, hasTouch: true, locale: "he-IL", ignoreHTTPSErrors: true } },
  { name: "desktop", options: { viewport: { width: 1440, height: 900 }, locale: "he-IL", ignoreHTTPSErrors: true } },
];

const browser = await chromium.launch();
try {
  for (const vp of VIEWPORTS) {
    console.log(`\n=== ${vp.name} ===`);
    const context = await browser.newContext(vp.options);
    await context.addCookies([{ name: args.cookieName, value: args.cookie, url: args.base, secure: true, httpOnly: true, sameSite: "Lax" }]);
    const page = await context.newPage();
    const problems = [];
    page.on("console", (m) => { if (/Content Security Policy|Refused to|Uncaught/.test(m.text())) problems.push(m.text()); });
    page.on("pageerror", (e) => problems.push("pageerror: " + e.message));

    // קישורים ישירים של גוגל: מזהה זוגי נחסם (כדי לבדוק את המעבר לנתיב שדרך השרת), אי-זוגי נטען.
    let blocked = 0;
    await page.route("https://lh3.googleusercontent.com/**", (route) => {
      const m = route.request().url().match(/id(\d+)x/);
      if (m && Number(m[1]) % 2 === 0) { blocked++; return route.abort(); }
      return route.fulfill({ status: 200, contentType: "image/png", body: directPng });
    });

    const tileCount = () => page.locator(".tile-image").count();
    const countText = () => page.textContent("#selection-count");
    const badgeOf = (name) => page.locator(`.tile-image[data-name="${name}"] .order-badge`).textContent();
    const noOverflow = async (label) => {
      const ok = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      assert(ok, `${vp.name}: אין גלילה לרוחב (${label})`);
    };

    // 1. מסך החיפוש הוא המסך הראשון
    await page.goto(args.base + "/app");
    assert(await page.isVisible("#search-input"), `${vp.name}: מסך החיפוש הוא המסך הראשון`);

    // 2. חיפוש עם גרש, וחלק ממילה שנייה בשם
    await page.fill("#search-input", "ג'ור");
    await page.waitForFunction(() => [...document.querySelectorAll(".result-name")].some((n) => n.textContent === "צימר ג'ורג' בגליל"));
    assert((await page.textContent(".result-parent")) === "בתוך: לוקיישנים", `${vp.name}: חיפוש עם גרש מוצא את הלוקיישן, עם שם תיקיית האב`);
    await page.fill("#search-input", "כרמ");
    await page.waitForFunction(() => [...document.querySelectorAll(".result-name")].some((n) => n.textContent === "וילה כרמל"));
    assert(true, `${vp.name}: "כרמ" מוצא את "וילה כרמל" (תחילת המילה השנייה)`);
    await page.fill("#search-input", "אין כזה");
    await page.waitForFunction(() => document.getElementById("search-status").textContent.includes("לא נמצאו"));
    assert(true, `${vp.name}: מצב "אין תוצאות"`);
    await page.route(/\/api\/drive\/search\?q=%D7%97%D7%9C%D7%A7%D7%99$/, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: [], nextPageToken: null, incomplete: true }) }));
    await page.fill("#search-input", "חלקי");
    await page.waitForFunction(() => document.getElementById("search-status").textContent.includes('"חלקי"'));
    assert((await page.textContent("#search-status")).includes("ייתכן שחסרות תוצאות"), `${vp.name}: חיפוש שגוגל לא השלים מסומן ככזה`);
    // מרוץ: תשובה איטית לחיפוש קודם ("צי") מגיעה אחרי התשובה לחיפוש החדש ("וילה"), ולא דורסת אותה.
    let releaseSlow;
    const slowGate = new Promise((resolve) => { releaseSlow = resolve; });
    await page.route(/\/api\/drive\/search\?q=%D7%A6%D7%99$/, async (route) => { await slowGate; await route.continue(); });
    await page.fill("#search-input", "צי");
    await page.waitForTimeout(600); // אחרי ההשהיה של החיפוש החי — הבקשה האיטית כבר יצאה
    await page.fill("#search-input", "וילה");
    await page.waitForFunction(() => document.getElementById("search-status").textContent === "נמצאה תיקייה אחת.");
    releaseSlow();
    await page.waitForTimeout(400);
    const afterRace = await page.$$eval(".result-name", (ns) => ns.map((n) => n.textContent));
    assert(afterRace.length === 1 && afterRace[0] === "וילה כרמל", `${vp.name}: תשובה מאוחרת של חיפוש קודם לא דורסת את התוצאות (${afterRace.join(", ")})`);
    assert(!(await page.isVisible("#selection")), `${vp.name}: פס הבחירה לא מוצג במסך החיפוש`);
    assert(!(await page.isVisible("#search-more")), `${vp.name}: "עוד תוצאות" מוסתר כשאין עוד`);
    await noOverflow("תוצאות חיפוש");
    await page.screenshot({ path: join(args.shots, `${vp.name}-1-search.png`), fullPage: true });

    // 3. כניסה ללוקיישן; הכותרת ממולאת בשם שלו
    await page.click(".result");
    await page.waitForFunction(() => document.getElementById("location-status").textContent.includes("תמונות"));
    assert((await page.textContent("#location-heading")) === "וילה כרמל", `${vp.name}: מסך הלוקיישן נפתח בלחיצה אחת`);
    assert((await page.inputValue("#title-input")) === "וילה כרמל", `${vp.name}: שדה הכותרת ממולא בשם הלוקיישן`);
    assert((await page.locator(".tile-folder").count()) === 2 && (await tileCount()) === 5, `${vp.name}: תתי-תיקיות ותמונות של הלוקיישן`);

    // 4. התמונות הממוזערות: ישיר, ובגיבוי דרך השרת
    await page.waitForFunction(() => [...document.querySelectorAll(".tile-image img")].every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 15000 });
    const srcs = await page.$$eval(".tile-image img", (imgs) => imgs.map((i) => i.getAttribute("src")));
    assert(srcs.some((s) => s.startsWith("/api/drive/thumbnail/")) && srcs.some((s) => s.startsWith("https://")),
      `${vp.name}: תמונה שנחסמת בקישור הישיר נטענת דרך השרת, והשאר ישירות (${blocked} נחסמו)`);

    const brokenNameOpacity = await page.evaluate(() => {
      const img = document.querySelector('.tile-image[data-name="סלון 5.jpg"] img');
      img.classList.add("thumb-broken");
      const opacity = getComputedStyle(img.nextElementSibling).opacity;
      img.classList.remove("thumb-broken");
      return opacity;
    });
    assert(brokenNameOpacity === "1", `${vp.name}: כשתמונה ממוזערת לא נטענת, השם מוצג במקומה`);

    // שלב 3: משבצת לרוחב (4:3) בלי חיתוך; בטלפון שתי עמודות
    const shape = await page.evaluate(() => {
      const t = document.querySelector(".tile-image");
      const r = t.getBoundingClientRect();
      return { ratio: r.width / r.height, fit: getComputedStyle(t.querySelector("img")).objectFit,
        cols: getComputedStyle(document.getElementById("grid")).gridTemplateColumns.split(" ").length };
    });
    assert(Math.abs(shape.ratio - 4 / 3) < 0.03 && shape.fit === "contain", `${vp.name}: משבצת לרוחב, התמונה בלי חיתוך (${shape.ratio.toFixed(2)}, ${shape.fit})`);
    if (vp.name === "phone") { assert(shape.cols === 2, `${vp.name}: שתי עמודות (${shape.cols})`); }

    const box = await page.locator(".tile-image").first().boundingBox();
    assert(box.width >= 44 && box.height >= 44, `${vp.name}: אריח בגודל נוח למגע (${Math.round(box.width)}px)`);

    // 5. בחירה ראשונה בתיקיית השורש
    await page.click('.tile-image[data-name="סלון 1.jpg"]');
    await page.click('.tile-image[data-name="סלון 2.jpg"]');
    assert((await badgeOf("סלון 1.jpg")) === "1" && (await badgeOf("סלון 2.jpg")) === "2", `${vp.name}: מספר סדר על כל תמונה`);
    assert((await countText()) === "נבחרו 2 מתוך 12", `${vp.name}: מונה הבחירה`);

    // 6. תיקייה עם 60 תמונות (שלושה עמודים)
    await page.click('.tile-folder:has-text("גינה")');
    await page.waitForFunction(() => document.getElementById("location-status").textContent === "60 תמונות", null, { timeout: 20000 });
    assert((await tileCount()) === 60, `${vp.name}: תיקייה עם יותר מ-50 תמונות מוצגת כולה (60)`);
    assert((await page.textContent(".crumb-current")) === "גינה", `${vp.name}: נתיב הניווט מציג את תת-התיקייה`);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: join(args.shots, `${vp.name}-3-folder-top.png`) });

    // 6א. כפתור ההגדלה: פותח בגדול בלי לבחור, מדפדף, ומאפשר לבחור מתוך התצוגה
    const zoomBox = await page.locator('.tile-image[data-name="גינה 3.jpg"] .tile-zoom').boundingBox();
    assert(zoomBox.width >= 44 && zoomBox.height >= 44, `${vp.name}: כפתור ההגדלה בגודל נוח למגע (${Math.round(zoomBox.width)}px)`);
    await page.click('.tile-image[data-name="גינה 3.jpg"] .tile-zoom');
    await page.waitForSelector("#viewer[open]");
    assert((await page.textContent("#viewer-name")) === "גינה 3" && (await page.textContent("#viewer-count")) === "3 מתוך 60",
      `${vp.name}: התצוגה המוגדלת נפתחת על התמונה שנלחצה`);
    assert((await countText()) === "נבחרו 2 מתוך 12", `${vp.name}: ההגדלה לא בוחרת את התמונה`);
    await page.waitForFunction(() => document.getElementById("viewer-high").classList.contains("loaded"), null, { timeout: 10000 });
    const highSrc = await page.getAttribute("#viewer-high", "src");
    assert(/=s1600$/.test(highSrc) || highSrc.endsWith("?size=large"), `${vp.name}: נטענת גרסה גדולה (${highSrc.slice(-20)})`);
    await page.waitForFunction(() => document.getElementById("viewer-low").classList.contains("loaded"), null, { timeout: 10000 });
    assert(true, `${vp.name}: גם התמונה הקטנה בתצוגה המוגדלת נטענת כשהקישור הישיר נחסם (בלי אייקון שבור)`);
    const nav = await page.evaluate(() => {
      const mid = window.innerWidth / 2;
      const prev = document.getElementById("viewer-prev").getBoundingClientRect();
      const next = document.getElementById("viewer-next").getBoundingClientRect();
      return { prevRight: prev.left > mid, nextLeft: next.right < mid };
    });
    await page.click("#viewer-next");
    await page.click("#viewer-prev"); // עכשיו "הקודמת" מוצגת
    const nav2 = await page.evaluate(() => {
      const prev = document.getElementById("viewer-prev").getBoundingClientRect();
      return prev.left > window.innerWidth / 2;
    });
    assert(nav.nextLeft && nav2, `${vp.name}: "הבאה" בצד שמאל ו"הקודמת" בצד ימין`);
    await page.screenshot({ path: join(args.shots, `${vp.name}-5-viewer.png`) });
    await page.click("#viewer-next");
    assert((await page.textContent("#viewer-name")) === "גינה 4", `${vp.name}: "הבאה" עוברת לתמונה הבאה`);
    await page.click("#viewer-select");
    assert((await countText()) === "נבחרו 3 מתוך 12" && (await badgeOf("גינה 4.jpg")) === "3", `${vp.name}: בחירה מתוך התצוגה המוגדלת`);
    await page.click("#viewer-select");
    assert((await countText()) === "נבחרו 2 מתוך 12", `${vp.name}: הסרה מתוך התצוגה המוגדלת`);
    await page.keyboard.press("Escape");
    assert(!(await page.isVisible("#viewer")), `${vp.name}: Escape סוגר את התצוגה המוגדלת`);

    // 7. בחירה מתת-תיקייה עד 12, וניסיון לבחור תמונה 13
    for (let i = 1; i <= 10; i++) { await page.click(`.tile-image[data-name="גינה ${i}.jpg"]`); }
    assert((await countText()) === "נבחרו 12 מתוך 12", `${vp.name}: בחירה מכמה תתי-תיקיות (2 + 10)`);
    await page.click('.tile-image[data-name="גינה 11.jpg"]');
    assert(await page.isVisible("#toast"), `${vp.name}: ניסיון לבחור תמונה 13 מציג הודעה`);
    assert((await page.textContent("#toast")).includes("עד 12"), `${vp.name}: ההודעה מסבירה את המגבלה`);
    assert((await countText()) === "נבחרו 12 מתוך 12" && (await badgeOf("גינה 11.jpg")) === "", `${vp.name}: תמונה 13 לא נבחרה`);
    assert((await page.locator("#selection-strip li").count()) === 12, `${vp.name}: רצועת הבחירה מציגה את כל 12`);
    await page.click('.tile-image[data-name="גינה 11.jpg"] .tile-zoom');
    await page.click("#viewer-select");
    assert((await page.textContent("#viewer-status")).includes("עד 12") && (await countText()) === "נבחרו 12 מתוך 12",
      `${vp.name}: גם בתצוגה המוגדלת אי אפשר לבחור תמונה 13, וההודעה מוצגת בתוכה`);
    await page.click("#viewer-close");
    await noOverflow("תיקייה עם 60 תמונות");
    await page.screenshot({ path: join(args.shots, `${vp.name}-2-selection.png`) });

    if (vp.name === "phone" || vp.name === "tablet") {
      // פס הבחירה הקבוע לא מסתיר את השורה האחרונה של התמונות
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const last = await page.locator(".tile-image").last().boundingBox();
      const bar = await page.locator("#selection").boundingBox();
      assert(last.y + last.height <= bar.y + 1, `${vp.name}: השורה האחרונה לא מוסתרת מאחורי פס הבחירה`);
    } else {
      const aside = await page.locator("#selection").boundingBox();
      const grid = await page.locator("#grid").boundingBox();
      assert(aside.x + aside.width <= grid.x + 1, `${vp.name}: לוח הבחירה בצד שמאל של הרשת`);
      await page.evaluate(() => window.scrollTo(0, 1500));
      const top = await page.evaluate(() => document.getElementById("selection").getBoundingClientRect().top);
      assert(Math.abs(top - 16) <= 1, `${vp.name}: לוח הבחירה נשאר צמוד לראש המסך בגלילה (top=${Math.round(top)})`);
      await page.evaluate(() => window.scrollTo(0, 0));
    }

    // 8. "אחורה" של הדפדפן חוזר לשורש הלוקיישן, והבחירה נשמרה
    await page.goBack({ waitUntil: "commit" });
    await page.waitForFunction(() => document.querySelectorAll(".tile-image").length === 5);
    assert((await badgeOf("סלון 1.jpg")) === "1" && (await badgeOf("סלון 2.jpg")) === "2", `${vp.name}: הבחירה נשמרת במעבר בין תתי-תיקיות`);

    // 9. רענון הדף לא מוחק את הבחירה
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll(".tile-image").length === 5);
    assert((await countText()) === "נבחרו 12 מתוך 12", `${vp.name}: רענון הדף שומר את הבחירה`);

    // 10. הסרה מצמצמת את המספור
    await page.click('.tile-image[data-name="סלון 1.jpg"]');
    assert((await badgeOf("סלון 1.jpg")) === "" && (await badgeOf("סלון 2.jpg")) === "1", `${vp.name}: הסרת תמונה מעדכנת את מספרי הסדר`);
    assert((await countText()) === "נבחרו 11 מתוך 12", `${vp.name}: המונה יורד אחרי הסרה`);

    // 11. כותרת שנערכה נשמרת
    await page.fill("#title-input", "וילה כרמל — הצעה");
    await page.reload();
    await page.waitForSelector(".tile-image");
    assert((await page.inputValue("#title-input")) === "וילה כרמל — הצעה", `${vp.name}: כותרת שנערכה נשמרת`);

    // 12. מעבר ללוקיישן אחר מאפס את הבחירה (אחרי אישור)
    await page.click("#back-button");
    await page.waitForSelector("#screen-search:not([hidden])");
    assert(!(await page.isVisible("#selection")) && !(await page.isVisible("#screen-location")), `${vp.name}: חזרה לחיפוש מסתירה את מסך הלוקיישן ואת פס הבחירה`);
    await page.fill("#search-input", "צימר");
    await page.waitForFunction(() => [...document.querySelectorAll(".result-name")].some((n) => n.textContent === "צימר ג'ורג' בגליל"));
    let dialogText = "";
    page.once("dialog", (d) => { dialogText = d.message(); d.accept(); });
    await page.click('.result:has-text("צימר ג\'ורג\' בגליל")');
    await page.waitForFunction(() => document.getElementById("location-heading").textContent === "צימר ג'ורג' בגליל");
    assert(dialogText.includes("11 התמונות"), `${vp.name}: לפני מעבר ללוקיישן אחר מוצגת אזהרה`);
    assert((await countText()) === "לא נבחרו תמונות", `${vp.name}: הבחירה מתאפסת במעבר ללוקיישן אחר`);
    assert((await page.inputValue("#title-input")) === "צימר ג'ורג' בגליל", `${vp.name}: הכותרת מתעדכנת ללוקיישן החדש`);

    // 13. "אחורה" של הדפדפן עד הלוקיישן הקודם: אותה אזהרה. ביטול משאיר את הבחירה ואת הכתובת.
    await page.waitForSelector('.tile-image[data-name="צימר 1.jpg"]');
    await page.click('.tile-image[data-name="צימר 1.jpg"]');
    await page.goBack({ waitUntil: "commit" }); // לחיפוש — בלי אזהרה, הבחירה לא נמחקת
    await page.waitForSelector("#screen-search:not([hidden])");
    const searchHashBefore = await page.evaluate(() => location.hash);
    const dialogSeen = new Promise((resolve) => page.once("dialog", (d) => { resolve(d.message()); d.dismiss(); }));
    await page.goBack({ waitUntil: "commit" }); // ל"וילה כרמל"
    const backText = await Promise.race([dialogSeen, new Promise((r) => setTimeout(() => r(null), 3000))]);
    assert(backText !== null && backText.includes("התמונה שנבחרה"), `${vp.name}: "אחורה" ללוקיישן אחר מציג אזהרה (${backText})`);
    await page.waitForTimeout(200);
    assert((await page.evaluate(() => location.hash)) === searchHashBefore && (await page.isVisible("#screen-search")),
      `${vp.name}: ביטול משאיר את המסך ואת הכתובת`);
    await page.click('.result:has-text("צימר ג\'ורג\' בגליל")'); // אותו לוקיישן — בלי אזהרה
    await page.waitForSelector('.tile-image[data-name="צימר 1.jpg"]');
    assert((await countText()) === "נבחרו 1 מתוך 12" && (await badgeOf("צימר 1.jpg")) === "1", `${vp.name}: אחרי ביטול הבחירה נשמרה`);
    // כתובת של לוקיישן אחר שהודבקה: אישור מאפס את הבחירה
    page.once("dialog", (d) => d.accept());
    await page.evaluate((id) => { location.hash = "#/l/" + id; }, args.ids.villa);
    await page.waitForFunction(() => document.getElementById("location-heading").textContent === "וילה כרמל");
    assert((await countText()) === "לא נבחרו תמונות", `${vp.name}: אחרי אישור הבחירה מתאפסת`);

    // ─── שלב 3: יצירה ושמירה ───
    const pdfCount = async () => (await (await page.request.get(args.base + "/_test/count")).json()).pdfs;
    const waitHeading = (text) => page.waitForFunction((t) => document.getElementById("job-heading").textContent === t, text, { timeout: 30000 });
    const stubShare = (filesSupported) => page.evaluate((files) => {
      // Chromium בלי ראש לא משתף קבצים; מדמים דפדפן טלפון שכן (files=true), או דפדפן שמשתף
      // רק קישורים וטקסט ו-canShare שלו מחזיר false על קבצים (files=false). לפי MDN.
      window.__shared = null;
      Object.defineProperty(navigator, "canShare", { configurable: true,
        value: (d) => !!(d && d.files && files && d.files.length === 1 && d.files[0].type === "application/pdf") });
      Object.defineProperty(navigator, "share", { configurable: true,
        value: (d) => { window.__shared = { name: d.files[0].name, size: d.files[0].size, type: d.files[0].type }; return Promise.resolve(); } });
    }, filesSupported);
    const withShare = vp.name !== "desktop";

    const createIdle = await page.$eval("#create-pdf", (b) => ({ disabled: b.disabled, cursor: getComputedStyle(b).cursor }));
    assert(createIdle.disabled && createIdle.cursor === "not-allowed", `${vp.name}: בלי בחירה, "יצירת PDF" נעול (${createIdle.cursor})`);

    // 14. יצירת PDF: חלון המתנה עם התקדמות, תוצאה, הורדה, קישור לדרייב, ושיתוף (או רק הורדה)
    for (const n of ["סלון 1.jpg", "סלון 2.jpg", "סלון 3.jpg"]) { await page.click(`.tile-image[data-name="${n}"]`); }
    await stubShare(withShare);
    let before = await pdfCount();
    await page.request.get(args.base + "/_test/slow?on=1"); // 1.2 שניות לתמונה: שאלת המצב הראשונה (אחרי 2 שניות) תופסת את ההתקדמות
    await page.click("#create-pdf");
    await page.waitForSelector("#job[open]");
    assert((await page.textContent(".job-lead")) === "מכין את הקובץ, זה יכול לקחת עד דקה." && (await page.isVisible(".job-lead")),
      `${vp.name}: "מכין את הקובץ, זה יכול לקחת עד דקה" מיד אחרי הלחיצה (${await page.textContent("#job-heading")} | ${await page.textContent("#job-message")})`);
    await page.waitForFunction(() => /מעבד תמונה \d מתוך 3/.test(document.getElementById("job-progress").textContent));
    assert(await page.isVisible("#job-running"), `${vp.name}: התקדמות בזמן היצירה ("מעבד תמונה N מתוך 3")`);
    await page.request.get(args.base + "/_test/slow?on=0");
    await waitHeading("הקובץ מוכן");
    const filename = await page.textContent("#job-filename");
    assert(/^וילה כרמל \d\d-\d\d-\d{4}\.pdf$/.test(filename), `${vp.name}: שם הקובץ (${filename})`);
    const order = await page.evaluate(() => {
      const [title, tail] = document.querySelectorAll("#job-filename bdi");
      return { tailLeftOfTitle: tail.getBoundingClientRect().right <= title.getBoundingClientRect().left + 1, tailDir: getComputedStyle(tail).direction };
    });
    assert(order.tailLeftOfTitle && order.tailDir === "ltr", `${vp.name}: שם הקובץ מוצג בסדר הנכון (הכותרת מימין, התאריך והסיומת משמאל)`);
    assert((await page.isVisible("#job-drive")) && (await page.getAttribute("#job-drive", "href")).startsWith("https://drive.google.com/"),
      `${vp.name}: קישור לקובץ שנשמר בדרייב`);
    assert((await pdfCount()) === before + 1, `${vp.name}: הקובץ נשמר בדרייב פעם אחת`);
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#job-download")]);
    assert(download.suggestedFilename() === filename, `${vp.name}: ההורדה בשם בעברית (${download.suggestedFilename()})`);
    if (withShare) {
      await page.waitForSelector("#job-share:not([hidden])");
      await page.click("#job-share");
      const shared = await page.evaluate(() => window.__shared);
      assert(shared && shared.name === filename && shared.type === "application/pdf" && shared.size > 1000,
        `${vp.name}: "שיתוף" פותח את חלון השיתוף עם קובץ ה-PDF (${shared && shared.size} בתים)`);
    } else {
      await page.waitForTimeout(500);
      assert(!(await page.isVisible("#job-share")) && (await page.isVisible("#job-download")), `${vp.name}: דפדפן שלא משתף קבצים — רק הורדה`);
    }
    await page.screenshot({ path: join(args.shots, `${vp.name}-4-result.png`) });
    await page.click("#job-close");
    assert(!(await page.isVisible("#job")), `${vp.name}: סגירת חלון התוצאה`);

    // 15. החיבור נופל באמצע היצירה (מסך נעול), והלשונית נטענת מחדש כשחוזרים אליה
    before = await pdfCount();
    await page.route("**/api/pdf/jobs", async (route) => {
      if (route.request().method() !== "POST") { return route.continue(); }
      route.fetch().catch(() => {});                      // הבקשה מגיעה לשרת...
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.abort("connectionreset");               // ...והדפדפן מאבד את החיבור
    });
    await page.click("#create-pdf");
    await page.waitForSelector("#job[open]");
    await page.waitForTimeout(400);
    await page.unroute("**/api/pdf/jobs");
    await page.reload();
    await waitHeading("הקובץ מוכן");
    assert((await pdfCount()) === before + 1, `${vp.name}: אחרי ניתוק וטעינה מחדש — הקובץ מוכן, ונשמר פעם אחת`);
    await page.click("#job-close");

    // 16. שתי לחיצות מהירות: קובץ אחד
    before = await pdfCount();
    await page.evaluate(() => { const b = document.getElementById("create-pdf"); b.click(); b.click(); });
    await waitHeading("הקובץ מוכן");
    await page.waitForTimeout(300);
    assert((await pdfCount()) === before + 1, `${vp.name}: שתי לחיצות מהירות יוצרות קובץ אחד`);
    await page.click("#job-close");

    // 17. השמירה בדרייב נכשלת: ההורדה עדיין זמינה, עם הודעה
    await page.request.get(args.base + "/_test/upload_error?on=1");
    await page.click("#create-pdf");
    await waitHeading("הקובץ מוכן");
    assert((await page.textContent("#job-message")).includes("לא נשמר בדרייב") && !(await page.isVisible("#job-drive")),
      `${vp.name}: שמירה שנכשלה מוצגת`);
    const [download2] = await Promise.all([page.waitForEvent("download"), page.click("#job-download")]);
    assert(download2.suggestedFilename().endsWith(".pdf"), `${vp.name}: וההורדה עדיין זמינה`);
    await page.request.get(args.base + "/_test/upload_error?on=0");
    await page.click("#job-close");

    // 18. ההרשאה בוטלה: הודעה עם קישור לחיבור מחדש
    await page.request.get(args.base + "/_test/revoked?on=1");
    await page.click("#create-pdf");
    await waitHeading("הקובץ לא נוצר");
    assert((await page.textContent("#job-message")).includes("צריך לחבר מחדש") && (await page.getAttribute("#job-message a", "href")) === "/settings",
      `${vp.name}: הרשאה שבוטלה — מסך חיבור מחדש`);
    await page.request.get(args.base + "/_test/revoked?on=0");
    await page.click("#job-close");
    await noOverflow("אחרי יצירה");

    assert(problems.length === 0, `${vp.name}: אין שגיאות JavaScript ואין הפרות CSP (${problems.join(" | ")})`);
    await context.close();
  }
  console.log("\nALL PICKER CHECKS PASSED");
} finally {
  await browser.close();
}
