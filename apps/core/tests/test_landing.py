"""Portada publica y modo demostracion."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_quien_llega_sin_sesion_ve_la_portada(client):
    """La portada es publica: no redirige al login."""
    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "El mostrador entero" in contenido
    assert "Illes CRM" in contenido
    # Y no se cuela nada del interior de la aplicacion.
    assert "Navegacion principal" not in contenido
    assert "selector-oficina" not in contenido


def test_la_portada_explica_los_modulos(client):
    contenido = client.get(reverse("core:home")).content.decode()

    for modulo in ("Reservas", "Disponibilidad", "Tarifas", "Cobros y caja", "Contratos"):
        assert modulo in contenido


def test_la_portada_lleva_al_login(client):
    contenido = client.get(reverse("core:home")).content.decode()

    assert reverse("accounts:login") in contenido


def test_con_sesion_la_misma_url_ensena_el_panel(client, agente_palma):
    """La portada es solo para visitantes: quien entra ve su trabajo."""
    client.force_login(agente_palma)

    contenido = client.get(reverse("core:home")).content.decode()

    assert "El mostrador entero" not in contenido
    assert "Reservas activas" in contenido


# ---------------------------------------------------------------------------
# Modo demostracion
# ---------------------------------------------------------------------------


def test_con_la_demo_apagada_no_se_ofrece(client, settings):
    settings.DEMO_MODE = False

    contenido = client.get(reverse("core:home")).content.decode()

    assert "Entrar en la demo" not in contenido
    assert reverse("core:demo_login") not in contenido


def test_con_la_demo_apagada_la_puerta_no_existe(client, settings):
    """Ni aunque alguien conozca la URL."""
    settings.DEMO_MODE = False

    respuesta = client.post(reverse("core:demo_login"))

    assert respuesta.status_code == 404


def test_con_la_demo_encendida_se_ofrece(client, settings):
    settings.DEMO_MODE = True

    contenido = client.get(reverse("core:home")).content.decode()

    assert "Entrar en la demo" in contenido
    assert settings.DEMO_EMAIL in contenido


def test_el_boton_de_demo_entra_de_verdad(client, settings, db):
    from apps.accounts.models import Role, User

    settings.DEMO_MODE = True
    settings.AXES_ENABLED = False
    rol = Role.objects.create(code="demo-rol", name="Demo")
    usuario = User.objects.create_user(email=settings.DEMO_EMAIL, password=settings.DEMO_PASSWORD)
    usuario.role = rol
    usuario.save()

    respuesta = client.post(reverse("core:demo_login"))

    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == reverse("core:home")
    assert respuesta.wsgi_request.user.is_authenticated


def test_sin_datos_de_demo_se_avisa_en_vez_de_reventar(client, settings):
    settings.DEMO_MODE = True

    respuesta = client.post(reverse("core:demo_login"))

    assert respuesta.status_code == 302
    assert reverse("accounts:login") in respuesta.headers["Location"]


def test_la_demo_solo_acepta_post(client, settings):
    settings.DEMO_MODE = True

    assert client.get(reverse("core:demo_login")).status_code == 405
