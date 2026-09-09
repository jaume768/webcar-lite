"""Contexto comun a todas las plantillas."""

from .navigation import sections_for
from .offices import available_offices, get_active_office


def ui(request):
    """Lo que necesita `base.html`: navegacion y oficina activa."""
    usuario = getattr(request, "user", None)
    return {
        "main_nav": sections_for(usuario),
        "available_offices": available_offices(request),
        "active_office": get_active_office(request),
    }
