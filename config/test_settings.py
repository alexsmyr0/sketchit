import os

os.environ.setdefault("MYSQL_DATABASE", "test_db")
os.environ.setdefault("MYSQL_USER", "test_user")
os.environ.setdefault("MYSQL_PASSWORD", "test_password")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/1")

from .settings import *

INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test.sqlite3",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
