"""wsgi.py — נקודת הכניסה של gunicorn (`wsgi:app`)."""

from web_app import create_app

app = create_app()
