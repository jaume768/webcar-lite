"""Vistas transversales. Aqui solo va lo que no pertenece a ningun dominio."""

import redis
import structlog
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = structlog.get_logger(__name__)


def _check_database() -> tuple[bool, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # el healthcheck reporta el fallo, no lo propaga
        return False, str(exc)
    return True, "ok"


def _check_redis() -> tuple[bool, str]:
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except Exception as exc:  # el healthcheck reporta el fallo, no lo propaga
        return False, str(exc)
    finally:
        client.close()
    return True, "ok"


@never_cache
@require_GET
def health(request):
    """Estado de las dependencias externas. 200 si todo responde, 503 si no."""
    checks = {}
    for name, check in (("database", _check_database), ("redis", _check_redis)):
        ok, detail = check()
        checks[name] = {"ok": ok, "detail": detail}

    healthy = all(item["ok"] for item in checks.values())
    if not healthy:
        failed = [name for name, item in checks.items() if not item["ok"]]
        logger.error("healthcheck_failed", failed=failed)

    return JsonResponse(
        {"status": "ok" if healthy else "error", "checks": checks},
        status=200 if healthy else 503,
    )


def home(request):
    """Portada provisional del esqueleto. La sustituira el panel de mostrador."""
    return render(request, "core/home.html")
