"""דרייב מזויף בזיכרון, עם ההתנהגות המתועדת שהבדיקות של שלב 0 נשענות עליה:

- `name contains` מוצא תחילת מילה בלבד (https://developers.google.com/workspace/drive/api/guides/ref-search-terms).
- escape של `\\'` ו-`\\\\` בשאילתה.
- עם drive.file + drive.readonly: כתיבה מותרת רק לקבצים שהאפליקציה יצרה, ואחרת 403.
"""

from __future__ import annotations

import itertools
import json
import re

import httplib2
from googleapiclient.errors import HttpError

FOLDER = "application/vnd.google-apps.folder"
_ids = itertools.count(1)


def _http_error(status: int, reason: str) -> HttpError:
    resp = httplib2.Response({"status": status})
    resp.reason = reason
    body = {"error": {"code": status, "message": reason, "errors": [{"domain": "global", "reason": reason, "message": reason}]}}
    return HttpError(resp, json.dumps(body).encode(), uri="https://www.googleapis.com/drive/v3/files")


def _validate_quoting(q: str) -> None:
    """כמו השרת של גוגל: ערך במירכאות חייב להסתיים בגרש שלא קודם לו escape."""
    i = 0
    while i < len(q):
        if q[i] == "'":
            i += 1
            while i < len(q) and q[i] != "'":
                i += 2 if q[i] == "\\" else 1
            if i >= len(q):
                raise _http_error(400, "invalid")
        i += 1


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDrive:
    def __init__(self) -> None:
        self.files_by_id: dict[str, dict] = {}
        self.queries: list[str] = []
        self.update_attempts: list[str] = []

    # ─── בנייה ───
    def add(self, name, mime=FOLDER, parent=None, *, owned=False, shared=True, can_edit=False, by_app=False, content=b""):
        fid = f"id{next(_ids):04d}x"
        self.files_by_id[fid] = {
            "id": fid, "name": name, "mimeType": mime, "parents": [parent] if parent else [],
            "ownedByMe": owned, "sharedWithMeTime": "2026-01-01T00:00:00Z" if shared and not owned else None,
            "capabilities": {"canEdit": can_edit}, "trashed": False, "by_app": by_app, "content": content,
            "thumbnailLink": f"https://lh3.googleusercontent.com/drive-storage/{fid}=s220" if mime.startswith("image/") else None,
        }
        return fid

    # ─── API ───
    def about(self):
        return self

    def files(self):
        return self

    def get(self, fileId=None, fields=None, supportsAllDrives=None, **kw):
        if fileId is None:  # about().get(fields=...)
            return _Exec(lambda: {"user": {"emailAddress": "owner@example.com"}})
        def run():
            f = self.files_by_id.get(fileId)
            if f is None:
                raise _http_error(404, "notFound")
            return {k: v for k, v in f.items() if k not in ("by_app", "content") and v is not None} | (
                {"size": str(len(f["content"]))} if f["mimeType"] != FOLDER else {}
            )
        return _Exec(run)

    def get_media(self, fileId=None, **kw):
        return _Exec(lambda: self.files_by_id[fileId]["content"])

    def list(self, q="", fields=None, pageSize=100, pageToken=None, orderBy=None, **kw):
        self.queries.append(q)

        def run():
            items = [f for f in self.files_by_id.values() if not f["trashed"]]
            if "mimeType = 'application/vnd.google-apps.folder'" in q and " or " not in q:
                items = [f for f in items if f["mimeType"] == FOLDER]
            if "mimeType != 'application/vnd.google-apps.folder'" in q:
                items = [f for f in items if f["mimeType"] != FOLDER]
            if " or " in q:
                allowed = set(re.findall(r"mimeType = '([^']+)'", q))
                items = [f for f in items if f["mimeType"] in allowed]
            m = re.search(r"'([A-Za-z0-9_-]+)' in parents", q)
            if m:
                items = [f for f in items if m.group(1) in f["parents"]]
            if "'me' in owners" in q:
                items = [f for f in items if f["ownedByMe"]]
            m = re.search(r"name contains '((?:[^'\\]|\\.)*)'", q)
            if m:
                needle = _unescape(m.group(1)).casefold()
                # התאמת קידומת: מהתחלת השם או מתחילת מילה
                items = [f for f in items if f["name"].casefold().startswith(needle)
                         or any(w.casefold().startswith(needle) for w in f["name"].split())]
            _validate_quoting(q)
            if orderBy == "folder,name":  # תיקיות קודם, ואז לפי שם — כמו שהקוד מבקש מדרייב
                items.sort(key=lambda f: (f["mimeType"] != FOLDER, f["name"]))
            elif orderBy is not None:
                raise AssertionError(f"unsupported orderBy in fake: {orderBy}")
            start = int(pageToken or 0)
            page = items[start:start + pageSize]
            out = {"files": [{k: v for k, v in f.items() if k not in ("by_app", "content") and v is not None} for f in page]}
            if start + pageSize < len(items):
                out["nextPageToken"] = str(start + pageSize)
            return out

        return _Exec(run)

    def create(self, body=None, media_body=None, fields=None, **kw):
        def run():
            content = b""
            if media_body is not None:
                fh = media_body._fd
                fh.seek(0)
                content = fh.read()
            parent = (body.get("parents") or [None])[0]
            fid = self.add(body["name"], body.get("mimeType", "text/plain"), parent, owned=True, shared=False,
                           can_edit=True, by_app=True, content=content)
            f = self.files_by_id[fid]
            return {"id": fid, "name": f["name"], "size": str(len(content)), "parents": f["parents"],
                    "webViewLink": f"https://drive.google.com/file/d/{fid}/view"}
        return _Exec(run)

    def update(self, fileId=None, body=None, **kw):
        def run():
            self.update_attempts.append(fileId)
            f = self.files_by_id[fileId]
            if not f["by_app"]:
                raise _http_error(403, "appNotAuthorizedToFile")
            f.update(body or {})
            return {"id": fileId}
        return _Exec(run)

    def delete(self, fileId=None, **kw):
        def run():
            f = self.files_by_id.get(fileId)
            if f is None:
                raise _http_error(404, "notFound")
            if not f["by_app"]:
                raise _http_error(403, "appNotAuthorizedToFile")
            del self.files_by_id[fileId]
            return ""
        return _Exec(run)
