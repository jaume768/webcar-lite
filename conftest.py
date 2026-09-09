"""Configuracion global de pytest."""

import pytest


@pytest.fixture(autouse=True)
def _sin_llamadas_externas(settings):
    """Ningun test debe salir a internet ni encolar tareas reales por accidente."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
