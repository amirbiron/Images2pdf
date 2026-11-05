"""אפליקציית WSGI זעירה לבדיקת gunicorn: /slow ישן N שניות (ברירת מחדל 6)."""

import time


def app(environ, start_response):
    if environ.get("PATH_INFO") == "/slow":
        time.sleep(float(environ.get("QUERY_STRING") or 6))
        body = b"slow-done"
    else:
        body = b"fast"
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]
