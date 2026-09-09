"""Utilidades para hablar con HTMX desde las vistas."""

import json

from django.http import HttpResponse

TOAST_EVENT = "toast"


def trigger_event(response: HttpResponse, event: str, detail=True) -> HttpResponse:
    """Anade un evento a HX-Trigger sin pisar los que ya vayan."""
    eventos = json.loads(response.headers.get("HX-Trigger", "{}"))
    eventos[event] = detail
    response.headers["HX-Trigger"] = json.dumps(eventos)
    return response


def trigger_toast(response: HttpResponse, message: str, level: str = "info") -> HttpResponse:
    """Adjunta un aviso a la respuesta via HX-Trigger.

    El cliente lo pinta como toast (ver static/js/app.js). Respeta los eventos
    que ya lleve la cabecera en lugar de pisarlos.
    """
    eventos = json.loads(response.headers.get("HX-Trigger", "{}"))
    eventos[TOAST_EVENT] = {"message": str(message), "level": level}
    response.headers["HX-Trigger"] = json.dumps(eventos)
    return response
