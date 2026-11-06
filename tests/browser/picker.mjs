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
  { name: "phone", options: { ...devices["iPhone 13"], locale: "he-IL" } },
  { name: "tablet", options: { viewport: { width: 800, height: 1280 }, hasTouch: true, locale: "he-IL" } },
  { name: "desktop", options: { viewport: { width: 1440, height: 900 }, locale: "he-IL" } },
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
    const badgeOf = (name) => page.locator(`.tile-image[data-name="${name}"] .badge`).textContent();
    const noOverflow = async (label) => {
      const ok = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      assert(ok, `${vp.name}: אין גלילה לרוחב (${label})`);
    };

    // 1. מסך החיפוש הוא המסך הראשון
    await page.goto(args.base + "/");
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

    // 7. בחירה מתת-תיקייה עד 12, וניסיון לבחור תמונה 13
    for (let i = 1; i <= 10; i++) { await page.click(`.tile-image[data-name="גינה ${i}.jpg"]`); }
    assert((await countText()) === "נבחרו 12 מתוך 12", `${vp.name}: בחירה מכמה תתי-תיקיות (2 + 10)`);
    await page.click('.tile-image[data-name="גינה 11.jpg"]');
    assert(await page.isVisible("#toast"), `${vp.name}: ניסיון לבחור תמונה 13 מציג הודעה`);
    assert((await page.textContent("#toast")).includes("עד 12"), `${vp.name}: ההודעה מסבירה את המגבלה`);
    assert((await countText()) === "נבחרו 12 מתוך 12" && (await badgeOf("גינה 11.jpg")) === "", `${vp.name}: תמונה 13 לא נבחרה`);
    assert((await page.locator("#selection-strip li").count()) === 12, `${vp.name}: רצועת הבחירה מציגה את כל 12`);
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

    assert(problems.length === 0, `${vp.name}: אין שגיאות JavaScript ואין הפרות CSP (${problems.join(" | ")})`);
    await context.close();
  }
  console.log("\nALL PICKER CHECKS PASSED");
} finally {
  await browser.close();
}
