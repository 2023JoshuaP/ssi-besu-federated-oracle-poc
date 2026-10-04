import os

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-verifier-secret")
DEBUG = False
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "django_verifier.urls"
MIDDLEWARE = []
INSTALLED_APPS = []
USE_TZ = True
DEFAULT_CHARSET = "utf-8"
