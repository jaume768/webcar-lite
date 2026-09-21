"""Ajustes de desarrollo y de tests. Postgres real, nunca SQLite."""

from .base import *  # noqa: F403
from .base import DATABASES, INSTALLED_APPS, MIDDLEWARE, STORAGES, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

# En dev el manifest de whitenoise estorba: exige collectstatic para cada cambio.
STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}

# Con clave de Brevo, tambien en desarrollo se envia de verdad (para probar).
EMAIL_BACKEND = (
    "apps.notifications.backends.BrevoEmailBackend"
    if env("BREVO_API_KEY", default="")
    else "django.core.mail.backends.console.EmailBackend"
)

# Ejecuta las tareas en el proceso salvo que se levante el worker aparte.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True

DATABASES["default"]["CONN_MAX_AGE"] = 0

if env.bool("DJANGO_DEBUG_TOOLBAR", default=False):
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1"]
