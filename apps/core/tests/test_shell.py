"""base.html y el shell: con usuario, sin usuario y con oficina activa."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_base_renderiza_sin_usuario(client):
    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Sin sesion iniciada" in contenido
    # El shell se pinta entero aunque no haya sesion.
    assert 'id="contenido"' in contenido
    assert "Navegacion principal" in contenido


def test_base_renderiza_con_usuario(client, django_user_model):
    usuario = django_user_model.objects.create_user(
        username="ana", password="secreto123", email="ana@ejemplo.es"
    )
    client.force_login(usuario)

    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "ana" in contenido
    assert "ana@ejemplo.es" in contenido
    assert "Sin sesion iniciada" not in contenido


def test_sin_oficinas_el_selector_lo_dice(client):
    respuesta = client.get(reverse("core:home"))

    assert "Sin oficinas asignadas" in respuesta.content.decode()


def test_selector_muestra_las_oficinas_disponibles(client):
    respuesta = client.get(reverse("core:ui_kit"))
    contenido = respuesta.content.decode()

    assert "Palma Centro" in contenido
    assert "Aeropuerto PMI" in contenido


def test_cambiar_de_oficina_actualiza_la_sesion(client):
    client.get(reverse("core:ui_kit"))  # deja las oficinas de demo en la sesion

    respuesta = client.post(
        reverse("core:set_active_office"),
        {"office_id": "aeropuerto"},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 200
    assert client.session["active_office_id"] == "aeropuerto"
    assert "Aeropuerto PMI" in respuesta.headers["HX-Trigger"]


def test_no_se_puede_activar_una_oficina_ajena(client):
    client.get(reverse("core:ui_kit"))

    respuesta = client.post(reverse("core:set_active_office"), {"office_id": "madrid"})

    assert respuesta.status_code == 403
    assert "active_office_id" not in client.session


def test_una_oficina_retirada_deja_de_estar_activa(client, settings):
    """Si la sesion apunta a una oficina que ya no esta permitida, se ignora."""
    from apps.core.offices import get_active_office

    peticion = client.get(reverse("core:ui_kit")).wsgi_request
    peticion.session["active_office_id"] = "aeropuerto"
    peticion.session["ui_offices"] = [{"id": "palma", "name": "Palma Centro"}]

    activa = get_active_office(peticion)

    assert activa.id == "palma"


def test_paginas_de_error_propias(client):
    for codigo in (403, 404):
        respuesta = client.get(reverse("core:ui_kit_error", args=[codigo]))
        assert respuesta.status_code == codigo
        assert str(codigo) in respuesta.content.decode()

    respuesta = client.get(reverse("core:ui_kit_error", args=[500]))
    assert respuesta.status_code == 500
    assert "Algo se ha roto" in respuesta.content.decode()
