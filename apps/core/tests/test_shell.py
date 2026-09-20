"""base.html y el shell: con usuario, sin usuario y con oficina activa."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_base_renderiza_sin_usuario(client):
    """La pantalla de acceso usa su propio esqueleto, sin menu ni oficinas."""
    respuesta = client.get(reverse("accounts:login"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Entrar" in contenido
    assert "Navegacion principal" not in contenido
    assert "selector-oficina" not in contenido


def test_base_renderiza_con_usuario(client, agente_centro):
    client.force_login(agente_centro)

    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert agente_centro.email in contenido
    assert 'id="contenido"' in contenido
    assert "Navegacion principal" in contenido


def test_el_menu_oculta_lo_que_el_usuario_no_puede_usar(client, agente_centro, gestor_centro):
    """Ocultar no sustituye a validar, pero tampoco se ensenan puertas cerradas."""
    client.force_login(agente_centro)
    sin_permiso = client.get(reverse("core:home")).content.decode()

    client.force_login(gestor_centro)
    con_permiso = client.get(reverse("core:home")).content.decode()

    assert "Usuarios" not in sin_permiso
    assert "Usuarios" in con_permiso


def test_sin_oficinas_el_selector_lo_dice(client, rol_mostrador):
    from apps.accounts.tests.factories import UserFactory

    huerfano = UserFactory(email="sinoficina@ejemplo.es", role=rol_mostrador)
    client.force_login(huerfano)

    respuesta = client.get(reverse("core:home"))

    assert "Sin oficinas asignadas" in respuesta.content.decode()


def test_el_selector_solo_muestra_las_oficinas_del_usuario(client, agente_centro, norte):
    """La oficina norte existe, pero este usuario no la tiene asignada."""
    client.force_login(agente_centro)

    contenido = client.get(reverse("core:home")).content.decode()

    assert "Oficina Centro" in contenido
    assert "Oficina Norte" not in contenido


def test_cambiar_de_oficina_actualiza_la_sesion(client, agente_centro, centro, norte):
    agente_centro.offices.add(norte)
    client.force_login(agente_centro)

    respuesta = client.post(
        reverse("core:set_active_office"),
        {"office_id": str(norte.pk)},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 200
    assert client.session["active_office_id"] == str(norte.pk)
    assert "Oficina Norte" in respuesta.headers["HX-Trigger"]


def test_no_se_puede_activar_una_oficina_ajena(client, agente_centro, norte):
    """Manipular el POST no abre la puerta a otra oficina."""
    client.force_login(agente_centro)

    respuesta = client.post(reverse("core:set_active_office"), {"office_id": str(norte.pk)})

    assert respuesta.status_code == 403
    assert "active_office_id" not in client.session


def test_una_oficina_retirada_deja_de_estar_activa(client, agente_centro, centro, norte):
    """Si dejan de asignarte una oficina, la sesion deja de valerte."""
    from apps.offices.selectors import get_active_office

    agente_centro.offices.add(norte)
    client.force_login(agente_centro)
    client.post(reverse("core:set_active_office"), {"office_id": str(norte.pk)})

    agente_centro.offices.remove(norte)
    peticion = client.get(reverse("core:home")).wsgi_request

    activa = get_active_office(peticion)
    assert activa == centro


def test_paginas_de_error_propias(client, usuario):
    client.force_login(usuario)

    for codigo in (403, 404):
        respuesta = client.get(reverse("core:ui_kit_error", args=[codigo]))
        assert respuesta.status_code == codigo
        assert str(codigo) in respuesta.content.decode()

    respuesta = client.get(reverse("core:ui_kit_error", args=[500]))
    assert respuesta.status_code == 500
    assert "Algo se ha roto" in respuesta.content.decode()
