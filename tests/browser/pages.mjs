// כניסה ראשונה, בחירת סיסמה, החלפה, עזרה ופרטיות — בגודל אחד (מגיע מ-tests/test_pages_browser.py).
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const args = JSON.parse(process.argv[2]);

function assert(cond, msg) { if (!cond) { throw new Error("FAILED: " + msg); } console.log("ok  - " + msg); }

const OPTIONS = {
  phone: { ...devices["iPhone 13"], locale: "he-IL", ignoreHTTPSErrors: true },
  tablet: { viewport: { width: 800, height: 1280 }, hasTouch: true, locale: "he-IL", ignoreHTTPSErrors: true },
  desktop: { viewport: { width: 1440, height: 900 }, locale: "he-IL", ignoreHTTPSErrors: true },
};
const vp = args.viewport;
const CHOSEN = "הסיסמה שלי 2026";
const NEXT = "סיסמה-שנייה-ארוכה";

const browser = await chromium.launch();
try {
  const context = await browser.newContext(OPTIONS[vp]);
  const page = await context.newPage();
  const problems = [];
  page.on("console", (m) => { if (/Content Security Policy|Refused to|Uncaught/.test(m.text())) problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  const noOverflow = async (label) => {
    const ok = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    assert(ok, `${vp}: אין גלילה לרוחב (${label})`);
  };

  // 1. דף הפרטיות פתוח בלי כניסה, בעברית ובאנגלית
  let resp = await page.goto(args.base + "/privacy");
  assert(resp.status() === 200 && (await page.isVisible("text=Privacy Policy")), `${vp}: /privacy נפתח בלי כניסה`);
  assert((await page.$eval("#en", (e) => getComputedStyle(e).direction)) === "ltr", `${vp}: החלק באנגלית משמאל לימין`);
  assert(await page.isVisible('a[href="mailto:privacy@example.org"]'), `${vp}: כתובת הקשר מגיעה ממשתנה הסביבה`);
  await noOverflow("פרטיות");
  await page.screenshot({ path: join(args.shots, `${vp}-privacy.png`) });

  // 2. דף הבית הציבורי (לאימות ה-Branding של גוגל): שם האפליקציה, תיאור, פרטיות וכניסה
  resp = await page.goto(args.base + "/");
  assert(resp.status() === 200 && new URL(page.url()).pathname === "/", `${vp}: דף הבית נפתח בלי כניסה ובלי הפניה`);
  assert((await page.title()).includes("images2pdf") && (await page.textContent("h1")) === "images2pdf", `${vp}: images2pdf בכותרת וב-<title>`);
  assert(await page.isVisible('.landing-actions a[href="/privacy"]') && await page.isVisible('.landing-actions a[href="/app"]'),
    `${vp}: קישורים בולטים לפרטיות ולכניסה`);
  await noOverflow("דף הבית");
  await page.screenshot({ path: join(args.shots, `${vp}-home.png`), fullPage: true });
  await page.click('.landing-actions a[href="/privacy"]');
  assert(new URL(page.url()).pathname === "/privacy", `${vp}: הקישור לפרטיות עובד בלי כניסה`);
  await page.goto(args.base + "/");

  // 3. "כניסה לאפליקציה" → מסך הכניסה → כניסה ראשונה עם הסיסמה מ-WEB_PANEL_SECRET → בחירת סיסמה
  await page.click('.landing-actions a[href="/app"]');
  await page.waitForURL((u) => u.pathname === "/login" && u.searchParams.get("next") === "/app");
  assert(true, `${vp}: "כניסה לאפליקציה" מוביל למסך הכניסה, עם חזרה לאפליקציה`);
  await noOverflow("כניסה");
  await page.fill("#current-password", args.envPassword);
  await Promise.all([page.waitForURL("**/password/setup"), page.click("button[type=submit]")]);
  assert(new URL(page.url()).search === "", `${vp}: הסיסמה לא מופיעה בכתובת`);
  await page.goto(args.base + "/help");
  assert(page.url().endsWith("/password/setup"), `${vp}: לפני בחירת סיסמה אי אפשר להגיע לשום דף אחר`);

  // 3. בחירת סיסמה: קצרה מדי נדחית, ואז סיסמה בעברית עם רווחים
  await page.fill("#new-password", "1234567");
  await page.fill("#confirm-password", "1234567");
  await page.evaluate(() => document.querySelectorAll("input").forEach((i) => i.removeAttribute("minlength")));
  await page.click("form[action='/password/setup'] button[type=submit]");
  assert((await page.textContent(".alert-error")).includes("לפחות 8"), `${vp}: סיסמה קצרה מ-8 נדחית עם הסבר`);
  await page.fill("#new-password", CHOSEN);
  await page.fill("#confirm-password", CHOSEN);
  await Promise.all([page.waitForURL((u) => u.pathname === "/app"), page.click("form[action='/password/setup'] button[type=submit]")]);
  assert((await page.textContent("main")).includes("הסיסמה נשמרה"), `${vp}: אחרי הבחירה — "הסיסמה נשמרה"`);

  // 4. התפריט: עזרה וסיסמה, בלי דפי פיתוח, ובלי גלילה לרוחב בטלפון
  const nav = await page.$$eval(".nav a", (as) => as.map((a) => a.getAttribute("href")));
  assert(nav.includes("/help") && nav.includes("/password") && !nav.some((h) => h.startsWith("/dev")), `${vp}: בתפריט עזרה וסיסמה, בלי דפי פיתוח (${nav.join(" ")})`);
  await noOverflow("מסך הבית עם התפריט");

  // 5. דף העזרה: קריא בטלפון
  await page.click('.nav a[href="/help"]');
  await page.waitForURL("**/help");
  const fontSize = await page.$eval(".prose p", (p) => parseFloat(getComputedStyle(p).fontSize));
  assert(fontSize >= 16, `${vp}: גודל הטקסט בעזרה ${fontSize}px`);
  await noOverflow("עזרה");
  await page.click('a[href="#reconnect"]');
  assert(await page.isVisible("#reconnect h2"), `${vp}: הקישורים לנושאים בראש העזרה עובדים`);
  const labels = await page.$$eval("#home bdi, #reconnect bdi", (bs) => bs.map((b) => [b.textContent, getComputedStyle(b).direction]));
  assert(labels.length >= 3 && labels.every(([, d]) => d === "ltr"), `${vp}: תוויות באנגלית בתוך העברית מבודדות (${labels.map(([t]) => t).join(", ")})`);
  await page.screenshot({ path: join(args.shots, `${vp}-help.png`), fullPage: true });
  await page.locator("#home").screenshot({ path: join(args.shots, `${vp}-help-home.png`) });

  // 6. החלפת סיסמה: המכשיר הזה נשאר מחובר, ומכשיר אחר מתנתק
  const other = await browser.newContext(OPTIONS[vp]);
  const otherPage = await other.newPage();
  await otherPage.goto(args.base + "/login");
  await otherPage.fill("#current-password", CHOSEN);
  await Promise.all([otherPage.waitForURL((u) => u.pathname === "/app"), otherPage.click("button[type=submit]")]);
  await page.goto(args.base + "/password");
  const ac = await page.$$eval("form[action='/password'] input", (is) => is.map((i) => `${i.name}:${i.autocomplete}`));
  assert(ac.includes("current_password:current-password") && ac.includes("new_password:new-password") && ac.includes("username:username"),
    `${vp}: שדות הטופס מסומנים לדפדפן (${ac.join(" ")})`);
  await page.fill("#current-password", CHOSEN);
  await page.fill("#new-password", NEXT);
  await page.fill("#confirm-password", NEXT);
  await page.click("form[action='/password'] button[type=submit]");
  await page.waitForSelector(".alert-ok");
  assert((await page.textContent(".alert-ok")).includes("הסיסמה הוחלפה"), `${vp}: הסיסמה הוחלפה`);
  await page.goto(args.base + "/settings");
  assert(new URL(page.url()).pathname === "/settings", `${vp}: המכשיר שבו החליפו נשאר מחובר`);
  await otherPage.goto(args.base + "/settings");
  assert(new URL(otherPage.url()).pathname === "/login", `${vp}: מכשיר אחר צריך להיכנס מחדש`);
  await otherPage.fill("#current-password", CHOSEN);
  await otherPage.click("button[type=submit]");
  assert((await otherPage.textContent(".alert-error")).includes("שגויה"), `${vp}: הסיסמה הקודמת כבר לא עובדת`);
  await other.close();

  // 7. קיצור הדרך למסך הבית: מניפסט שנפתח בדפדפן, ואייקון
  const manifestHref = await page.getAttribute('link[rel="manifest"]', "href");
  const manifest = await (await page.request.get(args.base + manifestHref)).json();
  assert(manifest.display === "browser", `${vp}: המניפסט פותח את הכלי בדפדפן (display: ${manifest.display})`);
  const icon = await page.request.get(args.base + (await page.getAttribute('link[rel="apple-touch-icon"]', "href")));
  assert(icon.status() === 200 && icon.headers()["content-type"] === "image/png", `${vp}: אייקון למסך הבית`);

  assert(problems.length === 0, `${vp}: אין שגיאות JavaScript ואין הפרות CSP (${problems.join(" | ")})`);
  await context.close();
  console.log("ALL PAGES CHECKS PASSED");
} finally {
  await browser.close();
}
