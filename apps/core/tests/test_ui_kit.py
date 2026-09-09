"""La tabla de 10.000 filas: busca, filtra y pagina sin recargar."""

import pytest
from django.urls import reverse

from apps.core import demo

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def test_el_catalogo_de_demo_tiene_diez_mil_filas():
    assert len(demo.catalogo()) == demo.TOTAL_FILAS == 10_000


def test_el_catalogo_es_determinista():
    assert demo.catalogo()[0] is demo.catalogo()[0]
    assert demo.catalogo()[42].plate == demo.catalogo()[42].plate


def test_la_pagina_completa_trae_el_shell_y_la_tabla(client):
    respuesta = client.get(reverse("core:ui_kit"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" in contenido
    assert 'id="tabla-vehiculos"' in contenido


def test_htmx_devuelve_solo_el_fragmento(client):
    """Sin esto, cada tecleo se traeria la pagina entera."""
    respuesta = client.get(reverse("core:ui_kit"), headers=HTMX)
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert 'id="tabla-vehiculos"' in contenido
    assert "<!DOCTYPE html>" not in contenido
    assert "Navegacion principal" not in contenido


def test_solo_se_pinta_una_pagina_de_filas(client):
    respuesta = client.get(reverse("core:ui_kit"), headers=HTMX)

    assert respuesta.content.decode().count("<tr>") == 26  # cabecera + 25 filas


def test_la_busqueda_filtra(client):
    respuesta = client.get(reverse("core:ui_kit"), {"q": "Ibiza"}, headers=HTMX)
    contenido = respuesta.content.decode()

    esperados = len(demo.filtrar(q="Ibiza"))
    assert 0 < esperados < demo.TOTAL_FILAS
    assert f"de {esperados}" in contenido
    assert "Ibiza" in contenido


def test_los_filtros_se_combinan(client):
    respuesta = client.get(
        reverse("core:ui_kit"), {"categoria": "F", "estado": "taller"}, headers=HTMX
    )

    esperados = len(demo.filtrar(categoria="F", estado="taller"))
    assert f"de {esperados}" in respuesta.content.decode()


def test_la_paginacion_avanza_conservando_el_filtro(client):
    primera = client.get(reverse("core:ui_kit"), {"q": "Seat"}, headers=HTMX)
    segunda = client.get(reverse("core:ui_kit"), {"q": "Seat", "page": 2}, headers=HTMX)

    assert primera.content != segunda.content
    assert "Pagina 2 de" in segunda.content.decode()
    # El enlace de la paginacion arrastra la busqueda.
    assert "q=Seat" in segunda.content.decode()


def test_una_pagina_invalida_no_rompe(client):
    """Un ?page= manipulado no puede dar un 500 en mostrador."""
    for valor in ("0", "abc", "999999", "-3"):
        respuesta = client.get(reverse("core:ui_kit"), {"page": valor}, headers=HTMX)
        assert respuesta.status_code == 200


def test_sin_resultados_se_muestra_el_estado_vacio(client):
    respuesta = client.get(reverse("core:ui_kit"), {"q": "zzzzzz"}, headers=HTMX)

    assert "Ningun vehiculo coincide" in respuesta.content.decode()


def test_el_autocompletado_devuelve_opciones(client):
    respuesta = client.get(reverse("core:ui_kit_vehicle_search"), {"q": "Ibiza"})
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert contenido.count('role="option"') == 10
    assert "Ibiza" in contenido


def test_el_autocompletado_sin_coincidencias(client):
    respuesta = client.get(reverse("core:ui_kit_vehicle_search"), {"q": "zzzzzz"})

    assert "Sin coincidencias" in respuesta.content.decode()


def test_el_modal_se_sirve_suelto(client):
    respuesta = client.get(reverse("core:ui_kit_modal"))
    contenido = respuesta.content.decode()

    assert 'role="dialog"' in contenido
    assert 'aria-modal="true"' in contenido
    assert "<!DOCTYPE html>" not in contenido


def test_la_accion_destructiva_responde_con_aviso(client):
    respuesta = client.post(reverse("core:ui_kit_destructive"), headers=HTMX)

    assert respuesta.status_code == 204
    assert "toast" in respuesta.headers["HX-Trigger"]
