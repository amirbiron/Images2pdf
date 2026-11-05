FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # להחזיר למערכת זיכרון ש-malloc שחרר, כדי לא להתקרב למגבלת 512MB (כמו בפאנל המקורי).
    MALLOC_TRIM_THRESHOLD_=131072

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ההגדרות (פורט, workers, timeouts) ב-gunicorn.conf.py, שנטען אוטומטית מ-/app.
CMD ["gunicorn", "wsgi:app"]
