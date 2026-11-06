// בדיקה בדפדפן אמיתי (Chromium) של המסלול המרכזי בשלב 0, במצב production:
// כניסה דרך הטופס עם סיסמה בעברית, עוגייה Secure, אין הפרות CSP,
// ובדיקה 5 (תמונות ממוזערות) מסמנת נכון מה נטען ומדווחת לשרת.
// הרצה:  node tests/browser/login_and_checks.mjs <path-to-gunicorn>
// גוגל לא מחובר כאן; תוצאות בדיקה מזויפות נכתבות לדיסק כדי לבדוק את ה-JS.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import net from "node:net";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const GUNICORN = process.argv[2] || "gunicorn";
const PASSWORD = "סיסמה-בעברית-123";

function freePort() {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
  });
}

function assert(cond, msg) { if (!cond) { throw new Error("FAILED: " + msg); } console.log("ok  - " + msg); }

const port = await freePort();
const dataDir = mkdtempSync(join(tmpdir(), "images2pdf-browser-"));
const base = `http://127.0.0.1:${port}`;
writeFileSync(join(dataDir, "dev_checks_last.json"), JSON.stringify({
  ran_at: "2026-11-05T10:00:00+00:00", folder_id: "sampleFolder123", fatal: null, elapsed_seconds: 1.0,
  checks: [{ num: 5, title: "התמונות הממוזערות מוצגות", status: "info", lines: [{ status: "info", text: "x" }] }],
  thumbnails: [
    { id: "imgOk1", name: "loads.png", direct: `${base}/static/logo.png`, direct_host_allowed: false },
    // מארח של גוגל (מותר ב-CSP), שהבקשה אליו נחסמת בבדיקה עם page.route
    { id: "imgBad2", name: "blocked.jpg", direct: "https://lh3.googleusercontent.com/blocked-in-test", direct_host_allowed: true },
  ],
  thumbnail_report: null,
}));

// PDF לדוגמה מוכן מראש, כדי לבדוק את ההורדה דרך לחיצה אמיתית (שם קובץ בעברית).
writeFileSync(join(dataDir, "dev_sample.pdf"), "%PDF-1.3\n%%EOF\n");
writeFileSync(join(dataDir, "dev_sample_last.json"), JSON.stringify({
  ok: true, ran_at: "2026-11-05T10:00:00+00:00", folder_id: "sampleFolder123", title: "וילה בכרמל",
  filename: "וילה בכרמל 05-11-2026.pdf", requested: 1, processed: 1, failed: [], orientation: "portrait", pages: 1,
  removed_characters: "", source_mb: 0.1, pdf_kb: 1, seconds: { total: 1, fetch: 0, process: 0, build: 0 },
  memory_mb: { rss_start: 1, rss_peak: 1, cgroup_start: null, cgroup_peak: null, samples: 1 },
}));

const server = spawn(GUNICORN, ["-c", join(ROOT, "gunicorn.conf.py"), "--bind", `127.0.0.1:${port}`, "wsgi:app"], {
  cwd: ROOT,
  env: { ...process.env, APP_ENV: "production", DATA_DIR: dataDir, WEB_PANEL_SECRET: PASSWORD, PORT: String(port) },
  stdio: ["ignore", "pipe", "pipe"],
});
let serverLog = "";
server.stdout.on("data", (d) => { serverLog += d; });
server.stderr.on("data", (d) => { serverLog += d; });

// בלי locale של UTF-8, Chromium מחליף כל שם קובץ שאינו ASCII ב-"download" (נמדד בסביבת הבדיקה).
const browser = await chromium.launch({ env: { ...process.env, LANG: "C.UTF-8", LC_ALL: "C.UTF-8" } });
try {
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(`${base}/healthz`); if (r.ok) break; } catch { /* עוד עולה */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  const cspViolations = [];
  page.on("console", (m) => { if (/Content Security Policy|Refused to/.test(m.text())) cspViolations.push(m.text()); });
  await page.route("https://lh3.googleusercontent.com/**", (route) => route.abort());

  await page.goto(`${base}/dev/checks`);
  assert(page.url().startsWith(`${base}/login?next=/dev/checks`), "לא מחובר → מועבר לכניסה");
  await page.fill("input[type=password]", PASSWORD);
  await Promise.all([page.waitForURL(`${base}/dev/checks`), page.click("button[type=submit]")]);
  assert(new URL(page.url()).search === "", "הסיסמה לא מופיעה בכתובת");

  const cookie = (await context.cookies()).find((c) => c.name === "pdf_panel_auth");
  assert(cookie && cookie.secure && cookie.httpOnly && cookie.sameSite === "Lax", "העוגייה נשמרה עם Secure, HttpOnly ו-SameSite=Lax");

  await page.waitForFunction(() => !document.getElementById("thumb-summary").textContent.includes("בודק"), null, { timeout: 30000 });
  const cells = await page.$$eval("tr[data-thumb-id]", (rows) => rows.map((r) => ({
    id: r.dataset.thumbId,
    direct: r.querySelector('[data-kind="direct"]').textContent,
    noref: r.querySelector('[data-kind="direct_no_referrer"]').textContent,
    proxy: r.querySelector('[data-kind="proxy"]').textContent,
  })));
  assert(cells[0].direct.includes("נטען") && cells[0].noref.includes("נטען"), "תמונה שנטענת מסומנת 'נטען'");
  assert(cells[1].direct.includes("נחסם"), "תמונה שלא נטענת מסומנת 'נחסם'");
  assert(cells.every((c) => c.proxy.includes("נחסם")), "בלי חיבור לגוגל, הנתיב דרך השרת מסומן 'נחסם'");
  const summary = await page.textContent("#thumb-summary");
  assert(summary.startsWith("נכשל"), "הסיכום לא מדווח הצלחה כשחלק מהתמונות לא נטענו");
  // תוויות הסטטוס (.badge) הן חלק מהשורה, ולא העיגול הממוקם של מספר הסדר בבורר.
  const badges = await page.$$eval(".check .badge", (bs) => bs.map((b) => getComputedStyle(b).position));
  assert(badges.length > 0 && badges.every((pos) => pos === "static"), `תוויות הסטטוס בדף הבדיקות במקומן (${badges.length}, ${[...new Set(badges)].join(",")})`);

  await page.waitForTimeout(500);
  const saved = JSON.parse(readFileSync(join(dataDir, "dev_checks_last.json"), "utf8"));
  assert(saved.thumbnail_report && saved.thumbnail_report.summary.direct.ok === 1 && saved.thumbnail_report.summary.direct.failed === 1,
    "הדיווח מהדפדפן נשמר בשרת");
  assert(/Thumbnail check from browser: direct ok=1 failed=1/.test(serverLog), "הסיכום נרשם בלוג של השרת");

  await page.screenshot({ path: process.env.SHOT || join(dataDir, "dev-checks.png"), fullPage: true });
  await page.goto(`${base}/dev/sample-pdf`);
  assert((await page.textContent("h1")).includes("PDF לדוגמה"), "הדף /dev/sample-pdf נטען אחרי כניסה");
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("a[href='/dev/sample-pdf/file']")]);
  assert(download.suggestedFilename() === "וילה בכרמל 05-11-2026.pdf", `ההורדה מקבלת את השם בעברית (${download.suggestedFilename()})`);
  await page.goto(`${base}/`);
  assert(cspViolations.length === 0, `אין הפרות CSP (${cspViolations.join(" | ")})`);
  console.log("ALL BROWSER CHECKS PASSED");
} finally {
  await browser.close();
  server.kill("SIGTERM");
}
