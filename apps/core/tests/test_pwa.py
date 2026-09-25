"""App instalable: manifest, service worker, pagina sin conexion y enlaces."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_el_manifest_es_publico_e_instalable(client):
    respuesta = client.get(reverse("manifest"))
    datos = respuesta.json()

    assert respuesta.status_code == 200
    assert respuesta["Content-Type"].startswith("application/manifest+json")
    assert datos["display"] == "standalone"
    assert datos["start_url"] == reverse("core:home")
    tamanos = {icono["sizes"] for icono in datos["icons"]}
    assert {"192x192", "512x512"} <= tamanos
    assert any(icono.get("purpose") == "maskable" for icono in datos["icons"])


def test_el_service_worker_se_sirve_desde_la_raiz(client):
    respuesta = client.get(reverse("service_worker"))
    codigo = respuesta.content.decode()

    assert reverse("service_worker") == "/sw.js"
    assert respuesta.status_code == 200
    assert respuesta["Content-Type"].startswith("application/javascript")
    assert respuesta["Service-Worker-Allowed"] == "/"
    assert reverse("offline") in codigo


def test_la_pagina_sin_conexion_no_lleva_nada_de_la_sesion(client, agente_centro):
    """La guarda el service worker: la veria cualquiera que abra la app sin red."""
    client.force_login(agente_centro)

    contenido = client.get(reverse("offline")).content.decode()

    assert "Sin conexión" in contenido
    assert agente_centro.email not in contenido
    assert "csrf" not in contenido.lower()


@pytest.mark.parametrize("nombre", ["accounts:login", "core:home"])
def test_las_pantallas_publicas_enlazan_el_manifest(client, nombre):
    contenido = client.get(reverse(nombre)).content.decode()

    assert f'rel="manifest" href="{reverse("manifest")}"' in contenido
    assert "js/pwa.js" in contenido


def test_el_panel_enlaza_el_manifest_y_ofrece_instalar(client, agente_centro):
    client.force_login(agente_centro)

    contenido = client.get(reverse("core:home")).content.decode()

    assert f'rel="manifest" href="{reverse("manifest")}"' in contenido
    assert "data-pwa-install" in contenido
