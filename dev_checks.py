"""
dev_checks.py — הדף /dev/checks (שלב 0 ב-ROADMAP). יוסר בשלב 4.

חמש הבדיקות:
  1. חיפוש מוצא תיקייה ששותפה עם החשבון, וגם תיקייה שנמצאת בתוכה.
  2. חיפוש עובד בעברית, עם חלק מהשם ועם גרש.
  3. אפשר ליצור תיקייה ולהעלות אליה קובץ קטן בהרשאות שנבחרו.
  4. ניסיון לשנות קובץ שהכלי לא יצר נכשל.
  5. התמונות הממוזערות מוצגות — קישור ישיר מול נתיב שעובר דרך השרת.
     בדיקה 5 רצה בדפדפן (dev_checks.js), כי רק הדפדפן אוכף חסימה של תמונה
     חיצונית. השרת לא רואה אותה.

הבדיקות שכותבות לדרייב (3, 4) רצות רק ב-POST. תוצאת הריצה האחרונה נשמרת בדיסק,
וסיכום בלי שמות קבצים נרשם בלוג (כדי שאפשר יהיה לקרוא אותו גם מהלוגים של Render).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from flask import Blueprint, current_app, jsonify, redirect, render_template, request
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from config import is_valid_drive_id
from google_api import (
    FOLDER_MIME,
    GoogleNotConnected,
    drive_create_folder,
    drive_get,
    drive_list_folder,
    drive_search_folders,
    drive_upload_bytes,
    get_drive_service,
    http_error_reason,
    http_status,
    is_allowed_thumbnail_url,
)
from storage import atomic_write_bytes, data_dir_problem

logger = logging.getLogger(__name__)

bp = Blueprint("dev_checks", __name__)

CHECKS_FOLDER_NAME = "בדיקות כלי PDF"
# שם קבוע עם גרש, כדי שבדיקה 2 תהיה דטרמיניסטית גם כשאין גרש בתיקיית הדוגמה.
GERESH_TEST_FOLDER_NAME = "צימר ג'ורג' בגליל"
SEARCH_MAX_PAGES = 10
TREE_MAX_FOLDERS = 150
THUMBNAILS_TO_TEST = 6

_HEBREW_RE = re.compile(r"[א-ת]")
_APOSTROPHES = ("'", "׳", "’")  # גרש ASCII, גרש עברי (׳), גרש מעוגל (’)

PASS, FAIL, WARN, INFO = "pass", "fail", "warn", "info"


@dataclass
class Line:
    status: str
    text: str


@dataclass
class CheckResult:
    num: int
    title: str
    status: str = INFO
    lines: list[Line] = field(default_factory=list)

    def add(self, status: str, text: str) -> None:
        self.lines.append(Line(status, text))


# ─── מצב שנשמר בין ריצות ─────────────────────────────────────

def _state_path():
    return current_app.extensions["images2pdf"].settings.data_dir / "dev_checks_state.json"


def _results_path():
    return current_app.extensions["images2pdf"].settings.data_dir / "dev_checks_last.json"


def _load_json(path) -> dict:
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read %s; starting fresh", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path, data: dict) -> None:
    atomic_write_bytes(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))


# ─── עזרים ────────────────────────────────────────────────────

def _search_contains(text: str, target_id: str) -> tuple[bool, int, bool]:
    """(נמצא?, כמה תוצאות נסרקו, האם החיפוש היה חלקי)."""
    token = None
    seen = 0
    incomplete = False
    for _ in range(SEARCH_MAX_PAGES):
        page = drive_search_folders(text, token)
        incomplete = incomplete or page["incompleteSearch"]
        for f in page["files"]:
            seen += 1
            if f.get("id") == target_id:
                return True, seen, incomplete
        token = page["nextPageToken"]
        if not token:
            break
    return False, seen, incomplete


def _http_error_text(exc: HttpError) -> str:
    reason = http_error_reason(exc)
    return f"HTTP {http_status(exc)}" + (f" ({reason})" if reason else "")


def _parent_name(item: dict) -> str | None:
    parents = item.get("parents")
    if not isinstance(parents, list) or not parents or not is_valid_drive_id(parents[0]):
        return None
    try:
        meta = drive_get(parents[0], "name")
    except HttpError:
        return None
    name = meta.get("name")
    return name if isinstance(name, str) else None


def _collect_tree(root: dict) -> list[dict]:
    """תיקיות מתחת לתיקיית הדוגמה (BFS, עד TREE_MAX_FOLDERS), כולל השורש."""
    folders = [root]
    queue = [root]
    while queue and len(folders) < TREE_MAX_FOLDERS:
        current = queue.pop(0)
        token = None
        while len(folders) < TREE_MAX_FOLDERS:
            page = drive_list_folder(current["id"], token, folders_only=True)
            for f in page["files"]:
                if isinstance(f.get("id"), str) and isinstance(f.get("name"), str):
                    folders.append(f)
                    queue.append(f)
            token = page["nextPageToken"]
            if not token:
                break
    return folders[:TREE_MAX_FOLDERS]


def _first_images(folder_id: str, limit: int) -> list[dict]:
    images: list[dict] = []
    token = None
    while len(images) < limit:
        page = drive_list_folder(folder_id, token)
        images.extend(f for f in page["files"] if f.get("mimeType") != FOLDER_MIME)
        token = page["nextPageToken"]
        if not token:
            break
    return images[:limit]


def _ensure_checks_folder(state: dict) -> tuple[str, bool]:
    """תיקיית הבדיקות בדרייב (נוצרת פעם אחת, המזהה נשמר בדיסק). (id, נוצרה עכשיו?)"""
    folder_id = state.get("folder_id")
    if is_valid_drive_id(folder_id):
        try:
            meta = drive_get(folder_id, "id, trashed")
            if meta.get("trashed") is not True:
                return folder_id, False
        except HttpError as exc:
            if http_status(exc) != 404:
                raise
    created = drive_create_folder(CHECKS_FOLDER_NAME)
    folder_id = created.get("id")
    if not is_valid_drive_id(folder_id):
        raise RuntimeError("folder creation returned no id")
    state["folder_id"] = folder_id
    state.pop("geresh_folder_id", None)
    return folder_id, True


# ─── הבדיקות ──────────────────────────────────────────────────

def check_1_search_shared(sample: dict, subfolders: list[dict]) -> CheckResult:
    r = CheckResult(1, "חיפוש מוצא תיקייה ששותפה עם החשבון, וגם תיקייה שנמצאת בתוכה")
    owned = sample.get("ownedByMe") is True
    shared_time = sample.get("sharedWithMeTime")
    if owned:
        r.add(WARN, "תיקיית הדוגמה בבעלות החשבון עצמו, ולא שותפה איתו. הבדיקה מוכיחה חיפוש בתיקייה בבעלות, לא בתיקייה משותפת.")
    elif isinstance(shared_time, str):
        r.add(INFO, "תיקיית הדוגמה שותפה עם החשבון (מופיעה ב\"שותף איתי\").")
    else:
        r.add(INFO, "תיקיית הדוגמה לא בבעלות החשבון ולא שותפה איתו ישירות (כנראה גישה דרך קישור).")

    found, seen, incomplete = _search_contains(sample["name"], sample["id"])
    r.add(PASS if found else FAIL, f"חיפוש לפי שם תיקיית הדוגמה \"{sample['name']}\": {'נמצאה' if found else 'לא נמצאה'} (נסרקו {seen} תוצאות).")
    if incomplete:
        r.add(WARN, "גוגל סימן את החיפוש כחלקי (incompleteSearch).")

    if not subfolders:
        r.add(WARN, "אין תתי-תיקיות בתיקיית הדוגמה, ולכן החלק השני לא נבדק.")
        r.status = WARN if found else FAIL
        return r

    sub = subfolders[0]
    sub_found, sub_seen, _ = _search_contains(sub["name"], sub["id"])
    r.add(
        PASS if sub_found else FAIL,
        f"חיפוש לפי שם תת-התיקייה \"{sub['name']}\": {'נמצאה' if sub_found else 'לא נמצאה'} (נסרקו {sub_seen} תוצאות בשם הזה או שמתחיל בו).",
    )
    if sub_found:
        parent = _parent_name(sub)
        r.add(INFO, f"שם תיקיית האב שאפשר להציג בתוצאות: \"{parent}\"." if parent else "לא הצלחנו לקרוא את שם תיקיית האב.")
    r.status = PASS if (found and sub_found) else FAIL
    if r.status == PASS and owned:
        r.status = WARN
    return r


def _word_cases(name: str) -> list[tuple[str, str, bool]]:
    """(תיאור, טקסט לחיפוש, האם זה חלק שחובה שיעבוד)."""
    words = [w for w in name.split() if w]
    cases: list[tuple[str, str, bool]] = [("השם המלא", name, True)]
    if words and len(words[0]) >= 3:
        cases.append(("תחילת המילה הראשונה", words[0][:3], True))
    if len(words) >= 2:
        cases.append(("מילה שנייה בשם, שלמה", words[1], False))
    long_word = next((w for w in words if len(w) >= 5), None)
    if long_word:
        cases.append(("אמצע מילה", long_word[1:4], False))
    return cases


def _geresh_query(name: str) -> str | None:
    for word in name.split():
        for i, ch in enumerate(word):
            if ch in _APOSTROPHES:
                return word[: i + 2] if i + 1 < len(word) else word
    return None


def check_2_hebrew(tree: list[dict], geresh_folder: dict | None, geresh_created_now: bool) -> CheckResult:
    r = CheckResult(2, "חיפוש עובד בעברית, עם חלק מהשם ועם גרש")
    required_ok = True

    def run(label: str, text: str, target: dict, required: bool) -> bool:
        nonlocal required_ok
        found, _, _ = _search_contains(text, target["id"])
        if required:
            required_ok = required_ok and found
            status = PASS if found else FAIL
        else:
            status = INFO
        r.add(status, f"{label}: חיפוש \"{text}\" ← {'נמצאה' if found else 'לא נמצאה'} \"{target['name']}\".")
        return found

    hebrew = next((f for f in tree if _HEBREW_RE.search(f["name"])), None)
    if hebrew is None:
        r.add(WARN, "אין בתיקיית הדוגמה תיקייה עם שם בעברית. נבדקת רק תיקיית הבדיקה עם הגרש.")
    else:
        for label, text, required in _word_cases(hebrew["name"]):
            run(f"תיקייה אמיתית, {label}", text, hebrew, required)

    real_geresh = next((f for f in tree if _geresh_query(f["name"])), None)
    if real_geresh is not None:
        run("תיקייה אמיתית עם גרש", _geresh_query(real_geresh["name"]), real_geresh, True)

    if geresh_folder is not None:
        q = _geresh_query(geresh_folder["name"])
        found = run("תיקיית בדיקה עם גרש (נוצרה על ידי הכלי)", q, geresh_folder, not geresh_created_now)
        if not found and geresh_created_now:
            r.add(WARN, "התיקייה נוצרה עכשיו, ולגוגל לוקח לפעמים זמן קצר לאנדקס אותה. מריצים שוב בעוד דקה.")
        for label, text, _ in _word_cases(geresh_folder["name"])[1:]:
            run(f"תיקיית הבדיקה, {label}", text, geresh_folder, False)
        alt = q.replace("'", "׳")
        run("אותו חיפוש עם גרש עברי (׳) במקום גרש רגיל", alt, geresh_folder, False)

    # גרש ולוכסן בתוך השאילתה לא אמורים לשבור אותה (שגיאה 400 = escape שבור).
    try:
        drive_search_folders("בדיקה'\\\"שבורה")
        r.add(PASS, "חיפוש עם גרש, לוכסן ומירכאות לא שובר את השאילתה.")
    except HttpError as exc:
        required_ok = False
        r.add(FAIL, f"חיפוש עם גרש ולוכסן נכשל: {_http_error_text(exc)}.")

    r.add(INFO, "לפי התיעוד של גוגל, name contains מחפש לפי תחילת מילה ולא באמצע מילה. השורות שמסומנות \"מידע\" בודקות את זה בפועל.")
    r.status = PASS if required_ok else FAIL
    return r


def check_3_create_upload(state: dict) -> CheckResult:
    r = CheckResult(3, "אפשר ליצור תיקייה ולהעלות אליה קובץ קטן בהרשאות שנבחרו")
    folder_id, created_now = _ensure_checks_folder(state)
    r.add(PASS, f"תיקיית \"{CHECKS_FOLDER_NAME}\" {'נוצרה עכשיו' if created_now else 'קיימת מריצה קודמת'}.")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S")
    content = f"קובץ בדיקה מ-/dev/checks, {stamp} UTC\n".encode("utf-8")
    uploaded = drive_upload_bytes(f"בדיקה {stamp}.txt", content, "text/plain", folder_id)
    new_id = uploaded.get("id")
    if not is_valid_drive_id(new_id):
        r.add(FAIL, "ההעלאה לא החזירה מזהה קובץ.")
        r.status = FAIL
        return r

    meta = drive_get(new_id, "id, size, parents")
    size_ok = str(meta.get("size")) == str(len(content))
    parent_ok = folder_id in (meta.get("parents") or [])
    downloaded = get_drive_service().files().get_media(fileId=new_id).execute()
    content_ok = downloaded == content
    ok = size_ok and parent_ok and content_ok
    r.add(PASS if ok else FAIL, "הקובץ הועלה, נמצא בתיקייה הנכונה, והתוכן שנקרא בחזרה זהה." if ok else
          f"הקובץ הועלה, אבל הבדיקה החוזרת נכשלה (גודל: {size_ok}, תיקייה: {parent_ok}, תוכן: {content_ok}).")

    previous = state.get("last_upload_id")
    state["last_upload_id"] = new_id
    if is_valid_drive_id(previous) and previous != new_id:
        try:
            get_drive_service().files().delete(fileId=previous).execute()
            r.add(INFO, "קובץ הבדיקה מהריצה הקודמת נמחק (הכלי יכול למחוק קבצים שהוא עצמו יצר).")
        except HttpError as exc:
            r.add(INFO, f"מחיקת קובץ הבדיקה הקודם לא הצליחה: {_http_error_text(exc)}.")
    link = uploaded.get("webViewLink")
    if isinstance(link, str) and link.startswith("https://"):
        r.add(INFO, f"הקובץ בדרייב: {link}")
    r.status = PASS if ok else FAIL
    return r


def _can_edit(item: dict) -> bool:
    caps = item.get("capabilities")
    return isinstance(caps, dict) and caps.get("canEdit") is True


def _attempt_modify(target: dict) -> tuple[str, str]:
    """ניסיון שינוי שאינו משנה כלום: מציבים לשם את אותו שם. (status, text)."""
    try:
        get_drive_service().files().update(
            fileId=target["id"], body={"name": target["name"]}, fields="id", supportsAllDrives=True
        ).execute()
    except HttpError as exc:
        if http_status(exc) == 403:
            return PASS, f"השינוי נחסם כמצופה: {_http_error_text(exc)}."
        return FAIL, f"שגיאה לא צפויה, ולכן הבדיקה לא הוכיחה חסימה: {_http_error_text(exc)}."
    return FAIL, "הכלי הצליח לשנות קובץ שהוא לא יצר (השם נשאר זהה). עוצרים ומדווחים לפני שממשיכים."


def check_4_cannot_modify(sample_images: list[dict], subfolders: list[dict], sample: dict, state: dict) -> CheckResult:
    r = CheckResult(4, "ניסיון לשנות קובץ שהכלי לא יצר נכשל")
    first = (sample_images or subfolders or [sample])[0]
    targets: list[dict] = [first]

    if not _can_edit(first):
        # קובץ שהמשתמש עצמו לא יכול לערוך ייחסם בכל מקרה, בלי קשר להרשאות של הכלי.
        # כדי להוכיח שההגבלה היא של הכלי, מחפשים קובץ שהחשבון כן יכול לערוך.
        checks_folder = state.get("folder_id")
        page = (
            get_drive_service()
            .files()
            .list(
                q=f"'me' in owners and mimeType != '{FOLDER_MIME}' and trashed = false",
                fields="files(id, name, parents, capabilities(canEdit))",
                pageSize=20,
            )
            .execute()
        )
        for f in page.get("files", []) if isinstance(page, dict) else []:
            if not isinstance(f, dict) or not is_valid_drive_id(f.get("id")) or not isinstance(f.get("name"), str):
                continue
            if checks_folder in (f.get("parents") or []):
                continue  # קבצים שהכלי יצר — הם מותרים לו
            if _can_edit(f):
                targets.append(f)
                break

    any_editable = False
    all_blocked = True
    for t in targets:
        editable = _can_edit(t)
        any_editable = any_editable or editable
        status, text = _attempt_modify(t)
        all_blocked = all_blocked and status == PASS
        who = "החשבון יכול לערוך אותו" if editable else "החשבון עצמו לא יכול לערוך אותו"
        r.add(status, f"\"{t['name']}\" ({who}): {text}")

    if not all_blocked:
        r.status = FAIL
    elif not any_editable:
        r.status = WARN
        r.add(WARN, "כל הקבצים שנבדקו חסומים גם למשתמש עצמו, ולכן החסימה לא מוכיחה שההגבלה היא של הכלי. "
                    "כדי להוכיח, צריך קובץ בבעלות החשבון שלא נוצר בכלי.")
    else:
        r.status = PASS
    return r


def check_5_thumbnails(images: list[dict]) -> tuple[CheckResult, list[dict]]:
    r = CheckResult(5, "התמונות הממוזערות מוצגות")
    items = []
    for img in images:
        link = img.get("thumbnailLink")
        items.append({
            "id": img["id"],
            "name": img["name"],
            "direct": link if isinstance(link, str) and link.startswith("https://") else None,
            "direct_host_allowed": is_allowed_thumbnail_url(link),
        })
    if not items:
        r.add(WARN, "לא נמצאו תמונות JPEG או PNG בתיקיית הדוגמה או בתת-התיקייה הראשונה שלה.")
        r.status = WARN
        return r, items
    missing = sum(1 for i in items if i["direct"] is None)
    if missing:
        r.add(WARN, f"ל-{missing} מתוך {len(items)} תמונות גוגל לא החזיר thumbnailLink.")
    unknown_host = sum(1 for i in items if i["direct"] and not i["direct_host_allowed"])
    if unknown_host:
        r.add(WARN, f"ל-{unknown_host} תמונות הקישור הוא במארח שהנתיב דרך השרת לא שולח אליו את ההרשאה.")
    r.add(INFO, "התוצאה של הבדיקה הזו נמדדת בדפדפן, בטבלה למטה.")
    r.status = INFO
    return r, items


# ─── ריצה ─────────────────────────────────────────────────────

def _guard(fn: Callable[[], CheckResult], num: int, title: str) -> CheckResult:
    """בדיקה שנכשלה בחריגה מדווחת ככישלון שלה בלבד, ושאר הבדיקות ממשיכות.

    RefreshError ו-GoogleNotConnected לא נבלעים כאן: הם עולים ל-error handler
    של האפליקציה, שמציג מסך "חיבור מחדש".
    """
    try:
        return fn()
    except (RefreshError, GoogleNotConnected):
        raise
    except HttpError as exc:
        logger.warning("Dev check %d failed with an HTTP error: %s", num, _http_error_text(exc))
        r = CheckResult(num, title, FAIL)
        r.add(FAIL, f"שגיאה מגוגל: {_http_error_text(exc)}.")
        return r
    except Exception:
        logger.exception("Dev check %d crashed", num)
        r = CheckResult(num, title, FAIL)
        r.add(FAIL, "הבדיקה נכשלה בשגיאה לא צפויה. הפרטים בלוג של השרת.")
        return r


def run_all(folder_id: str) -> dict:
    started = time.monotonic()
    drive = get_drive_service()
    about = drive.about().get(fields="user(emailAddress)").execute()  # בודק שהחיבור חי; RefreshError עולה למעלה
    del about

    try:
        sample = drive_get(folder_id, "id, name, mimeType, ownedByMe, sharedWithMeTime, parents, capabilities(canEdit)")
    except HttpError as exc:
        status = http_status(exc)
        logger.warning("Dev checks: sample folder not readable (%s)", _http_error_text(exc))
        return {
            "ran_at": _now(),
            "folder_id": folder_id,
            "fatal": "החשבון שחובר לא רואה את תיקיית הדוגמה." if status == 404 else f"שגיאה בקריאת תיקיית הדוגמה: {_http_error_text(exc)}.",
            "checks": [],
            "thumbnails": [],
        }
    if sample.get("mimeType") != FOLDER_MIME or not isinstance(sample.get("name"), str):
        return {"ran_at": _now(), "folder_id": folder_id, "fatal": "המזהה שהוזן אינו של תיקייה.", "checks": [], "thumbnails": []}

    state = _load_json(_state_path())
    subfolders = drive_list_folder(folder_id, folders_only=True)["files"]
    sample_images = _first_images(folder_id, THUMBNAILS_TO_TEST)
    if not sample_images and subfolders:
        sample_images = _first_images(subfolders[0]["id"], THUMBNAILS_TO_TEST)

    results: list[CheckResult] = []
    results.append(_guard(lambda: check_1_search_shared(sample, subfolders), 1, "חיפוש תיקייה משותפת ותת-תיקייה"))

    # בדיקה 3 לפני 2: היא יוצרת את תיקיית הבדיקות, ובתוכה את תיקיית הגרש של בדיקה 2.
    check3 = _guard(lambda: check_3_create_upload(state), 3, "יצירת תיקייה והעלאת קובץ")

    def _check2() -> CheckResult:
        tree = _collect_tree(sample)
        geresh_folder, created_now = None, False
        parent = state.get("folder_id")
        if is_valid_drive_id(parent):
            gid = state.get("geresh_folder_id")
            if is_valid_drive_id(gid):
                try:
                    meta = drive_get(gid, "id, name, trashed")
                    if meta.get("trashed") is not True and meta.get("name") == GERESH_TEST_FOLDER_NAME:
                        geresh_folder = {"id": gid, "name": GERESH_TEST_FOLDER_NAME}
                except HttpError as exc:
                    if http_status(exc) != 404:
                        raise
            if geresh_folder is None:
                created = drive_create_folder(GERESH_TEST_FOLDER_NAME, parent)
                if is_valid_drive_id(created.get("id")):
                    state["geresh_folder_id"] = created["id"]
                    geresh_folder = {"id": created["id"], "name": GERESH_TEST_FOLDER_NAME}
                    created_now = True
        return check_2_hebrew(tree, geresh_folder, created_now)

    results.append(_guard(_check2, 2, "חיפוש בעברית, חלק מהשם וגרש"))
    results.append(check3)
    results.append(_guard(lambda: check_4_cannot_modify(sample_images, subfolders, sample, state), 4, "ניסיון לשנות קובץ שהכלי לא יצר"))
    thumb_result, thumbs = check_5_thumbnails(sample_images)
    results.append(thumb_result)

    try:
        _save_json(_state_path(), state)
    except OSError:
        logger.warning("Could not save dev checks state", exc_info=True)

    results.sort(key=lambda c: c.num)
    elapsed = time.monotonic() - started
    logger.info(
        "Dev checks finished in %.1fs: %s",
        elapsed,
        ", ".join(f"{c.num}={c.status}" for c in results),
    )
    return {
        "ran_at": _now(),
        "folder_id": folder_id,
        "fatal": None,
        "elapsed_seconds": round(elapsed, 1),
        "checks": [asdict(c) for c in results],
        "thumbnails": thumbs,
        "thumbnail_report": None,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── נתיבים ───────────────────────────────────────────────────

@bp.get("/dev/checks")
def page():
    services = current_app.extensions["images2pdf"]
    disk_problem = data_dir_problem(services.settings.data_dir)
    last = _load_json(_results_path()) if disk_problem is None else {}
    return render_template(
        "dev_checks.html",
        last=last or None,
        default_folder_id=(last or {}).get("folder_id") or services.settings.dev_sample_folder_id or "",
        disk_problem=disk_problem,
        google_problems=services.settings.google_problems,
        # כשהדיסק לא תקין לא נוגעים בקובץ ההרשאה (stat עלול לזרוק), כדי שהדף יציג את הבעיה.
        connected=disk_problem is None and services.token_store is not None and services.token_store.version() is not None,
    )


@bp.post("/dev/checks/run")
def run():
    folder_id = (request.form.get("folder_id") or "").strip()
    if not is_valid_drive_id(folder_id):
        return render_template("message.html", title="מזהה תיקייה לא תקין",
                               message="מזהה תיקייה מכיל רק אותיות באנגלית, ספרות, מקף וקו תחתון.",
                               hint="מעתיקים את החלק שאחרי folders/ בכתובת של התיקייה בדרייב."), 400
    results = run_all(folder_id)
    try:
        _save_json(_results_path(), results)
    except OSError:
        logger.error("Could not save dev checks results", exc_info=True)
        return render_template("message.html", title="התוצאות לא נשמרו",
                               message="הבדיקות רצו, אבל השמירה לדיסק נכשלה.",
                               hint="בודקים ב-Render שהדיסק הקבוע מחובר ב-/var/data."), 500
    return redirect("/dev/checks#results", code=303)


_REPORT_KEYS = ("direct", "direct_no_referrer", "proxy")


@bp.post("/dev/checks/thumbnail-report")
def thumbnail_report():
    """הדפדפן מדווח מה נטען. נשמר לתוצאות ונרשם בלוג (מספרים בלבד)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list) or len(data["results"]) > 50:
        return jsonify({"error": "bad_request"}), 400
    summary = {k: {"ok": 0, "failed": 0} for k in _REPORT_KEYS}
    clean = []
    for item in data["results"]:
        if not isinstance(item, dict) or not is_valid_drive_id(item.get("id")):
            return jsonify({"error": "bad_request"}), 400
        row = {"id": item["id"]}
        for k in _REPORT_KEYS:
            v = item.get(k)
            if v not in (True, False, None):
                return jsonify({"error": "bad_request"}), 400
            row[k] = v
            if v is True:
                summary[k]["ok"] += 1
            elif v is False:
                summary[k]["failed"] += 1
        clean.append(row)
    ua = request.headers.get("User-Agent", "")[:200]
    logger.info(
        "Thumbnail check from browser: direct ok=%d failed=%d; direct(no-referrer) ok=%d failed=%d; proxy ok=%d failed=%d; UA=%s",
        summary["direct"]["ok"], summary["direct"]["failed"],
        summary["direct_no_referrer"]["ok"], summary["direct_no_referrer"]["failed"],
        summary["proxy"]["ok"], summary["proxy"]["failed"],
        ua,
    )
    last = _load_json(_results_path())
    if last:
        last["thumbnail_report"] = {"at": _now(), "summary": summary, "results": clean, "user_agent": ua}
        try:
            _save_json(_results_path(), last)
        except OSError:
            logger.warning("Could not save the thumbnail report", exc_info=True)
            return jsonify({"saved": False}), 500
    return jsonify({"saved": bool(last), "summary": summary})
