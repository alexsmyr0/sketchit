import os

# Set dummy env vars for settings.py to avoid RuntimeError
os.environ.setdefault('MYSQL_DATABASE', 'dummy')
os.environ.setdefault('MYSQL_USER', 'dummy')
os.environ.setdefault('MYSQL_PASSWORD', 'dummy')
os.environ.setdefault('MYSQL_HOST', 'dummy')
os.environ.setdefault('MYSQL_PORT', '3306')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')
os.environ.setdefault('SECRET_KEY', 'dummy-secret-key')

from config.settings import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'test_db.sqlite3',
        'OPTIONS': {
            'timeout': 30,
        }
    }
}

# Use local memory cache for tests instead of django_redis
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Ensure we don't have stray session issues
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# Enable runtime coordinator to support protocol-level sync events in integration tests.
SKETCHIT_ENABLE_RUNTIME_COORDINATOR = True
