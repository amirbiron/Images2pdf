// static/form_wait.js בדפדפן אמיתי: נעילה והודעה בשליחה, ושחרור כשהדף משוחזר מ-bfcache.
// הרצה: node tests/browser/form_wait.mjs
// שחזור אמיתי מ-bfcache לא ניתן לאילוץ ב-headless, ולכן שולחים את אותו אירוע שהדפדפן
// שולח בשחזור: PageTransitionEvent("pageshow", { persisted: true }).
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function assert(cond, msg) { if (!cond) { throw new Error("FAILED: " + msg); } console.log("ok  - " + msg); }

const browser = await chromium.launch();
try {
  for (const [form, button, wait] of [["sample-form", "sample-button", "sample-wait"], ["run-form", "run-button", "run-wait"]]) {
    const page = await browser.newPage();
    await page.setContent(`<form id="${form}"><button id="${button}" type="submit">שלח</button>
      <p id="${wait}" role="status" data-wait="מכין את הקובץ"></p></form>`);
    await page.addScriptTag({ path: join(ROOT, "static", "form_wait.js") });
    // מונעים ניווט אמיתי, אחרי שהמאזין של הסקריפט כבר רץ
    await page.evaluate((id) => document.getElementById(id).addEventListener("submit", (e) => e.preventDefault()), form);

    await page.click(`#${button}`);
    assert(await page.$eval(`#${button}`, (b) => b.disabled), `${form}: הכפתור ננעל בשליחה`);
    assert((await page.textContent(`#${wait}`)) === "מכין את הקובץ", `${form}: הודעת ההמתנה נכתבת לאזור ה-status`);

    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false })));
    assert(await page.$eval(`#${button}`, (b) => b.disabled), `${form}: טעינה רגילה לא משחררת באמצע שליחה`);

    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    assert(!(await page.$eval(`#${button}`, (b) => b.disabled)), `${form}: שחזור מ-bfcache משחרר את הכפתור`);
    assert((await page.textContent(`#${wait}`)) === "", `${form}: ושם מנקה את הודעת ההמתנה`);
    await page.close();
  }
  console.log("ALL FORM CHECKS PASSED");
} finally {
  await browser.close();
}
