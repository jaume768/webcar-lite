"""Oficina activa: de donde sale la lista y como se cambia.

El proveedor de la lista se configura con `CORE_OFFICE_PROVIDER`, para que este
modulo no dependa de `apps.offices`: core no sabe de dominio. La validacion del
cambio, en cambio, si vive aqui, porque es la misma para cualquier proveedor.
"""

from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string

SESSION_ACTIVE_OFFICE_KEY = "active_office_id"


@dataclass(frozen=True, slots=True)
class OfficeChoice:
    """Lo minimo que necesita la interfaz para pintar y cambiar de oficina."""

    id: str
    name: str


@lru_cache(maxsize=4)
def _provider(ruta: str):
    return import_string(ruta)


def available_offices(request) -> list[OfficeChoice]:
    """Oficinas sobre las que el usuario puede operar. Punto unico de verdad:
    el selector, la validacion del cambio y el scope de datos leen de aqui."""
    return _provider(settings.CORE_OFFICE_PROVIDER)(request)


def get_active_office(request) -> OfficeChoice | None:
    """Oficina activa, siempre contrastada contra las disponibles.

    Si la sesion apunta a una oficina que ya no esta permitida se ignora: no se
    devuelve nunca una oficina que el usuario no pueda usar.
    """
    offices = available_offices(request)
    if not offices:
        return None

    active_id = request.session.get(SESSION_ACTIVE_OFFICE_KEY)
    for office in offices:
        if office.id == active_id:
            return office
    return offices[0]


def set_active_office(request, office_id: str) -> OfficeChoice:
    """Cambia la oficina activa. Lanza ValueError si no esta permitida."""
    for office in available_offices(request):
        if office.id == office_id:
            request.session[SESSION_ACTIVE_OFFICE_KEY] = office.id
            return office
    raise ValueError(f"Oficina no disponible para este usuario: {office_id!r}")
