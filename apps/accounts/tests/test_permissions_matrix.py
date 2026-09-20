"""Matriz permiso x endpoint.

Ocultar un boton no es validar. Cada vista protegida se prueba con un usuario
que ha iniciado sesion pero no tiene el permiso: la respuesta tiene que ser 403,
tambien cuando el POST se manda a mano contra el endpoint.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _url(nombre, usuario_objetivo=None):
    if usuario_objetivo is None:
        return reverse(nombre)
    return reverse(nombre, args=[usuario_objetivo.pk])


#: (nombre de la vista, metodo, necesita un usuario en la URL, permiso exigido)
ENDPOINTS_PROTEGIDOS = [
    ("accounts:user_list", "get", False, "accounts.manage_users"),
    ("accounts:user_create", "get", False, "accounts.manage_users"),
    ("accounts:user_create", "post", False, "accounts.manage_users"),
    ("accounts:user_update", "get", True, "accounts.manage_users"),
    ("accounts:user_update", "post", True, "accounts.manage_users"),
    ("accounts:user_deactivate", "post", True, "accounts.manage_users"),
    ("accounts:user_activate", "post", True, "accounts.manage_users"),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objetivo", "permiso"), ENDPOINTS_PROTEGIDOS)
def test_sin_permiso_devuelve_403(client, agente_centro, vista, metodo, con_objetivo, permiso):
    client.force_login(agente_centro)
    assert not agente_centro.has_perm(permiso)

    url = _url(vista, agente_centro if con_objetivo else None)
    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code == 403, f"{metodo.upper()} {url} deberia dar 403"


@pytest.mark.parametrize(("vista", "metodo", "con_objetivo", "permiso"), ENDPOINTS_PROTEGIDOS)
def test_sin_sesion_no_se_llega_al_endpoint(
    client, agente_centro, vista, metodo, con_objetivo, permiso
):
    url = _url(vista, agente_centro if con_objetivo else None)

    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code == 302
    assert reverse("accounts:login") in respuesta.headers["Location"]


@pytest.mark.parametrize(("vista", "metodo", "con_objetivo", "permiso"), ENDPOINTS_PROTEGIDOS)
def test_con_permiso_no_da_403(client, gestor_centro, vista, metodo, con_objetivo, permiso):
    """Contraprueba: sin esto, la matriz pasaria aunque todo diera 403 siempre."""
    client.force_login(gestor_centro)
    assert gestor_centro.has_perm(permiso)

    url = _url(vista, gestor_centro if con_objetivo else None)
    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code != 403


def test_el_permiso_llega_por_el_rol_no_por_el_usuario(gestor_centro):
    """Los permisos viven en el rol; el usuario no los tiene asignados a mano."""
    assert gestor_centro.user_permissions.count() == 0
    assert gestor_centro.has_perm("accounts.manage_users")


def test_un_usuario_desactivado_pierde_los_permisos(gestor_centro):
    gestor_centro.is_active = False
    gestor_centro.save(update_fields=["is_active"])
    gestor_centro = type(gestor_centro).objects.get(pk=gestor_centro.pk)

    assert not gestor_centro.has_perm("accounts.manage_users")


def test_el_superusuario_no_necesita_rol(superusuario):
    assert superusuario.role is None
    assert superusuario.has_perm("accounts.manage_users")
    assert superusuario.has_perm("reservations.cancel_reservation")
