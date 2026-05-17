"""Minimal Django settings for fixture."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "fixture-only-not-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]

ROOT_URLCONF = "urls"
WSGI_APPLICATION = None

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.admin",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
