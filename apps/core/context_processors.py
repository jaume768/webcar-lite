"""Contexto comun a todas las plantillas."""

from .navigation import MAIN_NAV
from .offices import available_offices, get_active_office


def ui(request):
    """Lo que necesita `base.html`: navegacion y oficina activa."""
    return {
        "main_nav": MAIN_NAV,
        "available_offices": available_offices(request),
        "active_office": get_active_office(request),
    }
