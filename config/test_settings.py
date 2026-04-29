import os

# This is the only supported Django test settings module. It intentionally
# keeps the MySQL database backend from config.settings so tests exercise the
# same database engine as development and runtime.
os.environ["MYSQL_DATABASE"] = "sketchit"
os.environ["MYSQL_USER"] = "root"
os.environ["MYSQL_PASSWORD"] = "sketchit-root"
os.environ["MYSQL_HOST"] = "mysql"
os.environ["REDIS_URL"] = "redis://redis:6379/1"

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

# Most tests exercise synchronous service behavior and should not spawn
# background timer threads.
SKETCHIT_ENABLE_RUNTIME_COORDINATOR = False
