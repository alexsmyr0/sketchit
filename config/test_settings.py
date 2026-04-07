import os

os.environ.setdefault("MYSQL_DATABASE", "test_db")
os.environ.setdefault("MYSQL_USER", "test_user")
os.environ.setdefault("MYSQL_PASSWORD", "test_password")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/1")

from .settings import *

INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]

# Tests inherit the MySQL database config from config.settings.
# This module only swaps Channels to the in-memory layer so Redis is not
# required for test cases that do not exercise Redis transport behavior.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
