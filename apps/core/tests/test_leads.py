"""Contactos que llegan de la portada publica.

La portada es la unica pantalla que ve alguien de fuera. Si el formulario no
guarda, se pierde el cliente: por eso el contacto se escribe siempre y el aviso
por correo es un extra que puede fallar.
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.core.leads import register_lead, whatsapp_link
from apps.core.models import Lead, LeadStatus

pytestmark = pytest.mark.django_db


DATOS = {
    "name": "Marta Gil",
    "company": "Gil Rent a Car",
    "phone": "600 111 222",
    "fleet_size": "12",
    "message": "Tenemos dos oficinas en Denia.",
}


# ---------------------------------------------------------------------------
# Enlace de WhatsApp (sin base de datos)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numero", "esperado"),
    [
        ("683472110", "https://wa.me/34683472110"),
        ("+34 683 47 21 10", "https://wa.me/34683472110"),
        ("34683472110", "https://wa.me/34683472110"),
        ("", ""),
    ],
)
def test_el_enlace_de_whatsapp_se_arma_con_el_prefijo(numero, esperado):
    assert whatsapp_link(numero) == esperado


def test_sin_whatsapp_configurado_no_hay_enlace(settings):
    settings.CONTACT_WHATSAPP = ""

    assert whatsapp_link() == ""


# ---------------------------------------------------------------------------
# Alta del contacto
# ---------------------------------------------------------------------------


def test_el_formulario_guarda_el_contacto(client):
    respuesta = client.post(reverse("core:lead_create"), DATOS)

    assert respuesta.status_code == 200
    lead = Lead.objects.get()
    assert lead.name == "Marta Gil"
    assert lead.company == "Gil Rent a Car"
    assert lead.fleet_size == 12
    assert lead.status == LeadStatus.NEW
    assert "Recibido" in respuesta.content.decode()


def test_sin_telefono_ni_correo_no_se_guarda(client):
    respuesta = client.post(reverse("core:lead_create"), {"name": "Marta Gil"})

    assert respuesta.status_code == 422
    assert not Lead.objects.exists()
    assert "no podemos contestarte" in respuesta.content.decode()


def test_el_campo_trampa_descarta_al_robot(client):
    respuesta = client.post(
        reverse("core:lead_create"), {**DATOS, "website": "http://spam.example"}
    )

    # Se le responde como a cualquiera para no ensenarle que le hemos pillado.
    assert respuesta.status_code == 200
    assert not Lead.objects.exists()


def test_al_contacto_no_se_llega_por_get(client):
    assert client.get(reverse("core:lead_create")).status_code == 405


# ---------------------------------------------------------------------------
# Aviso al comercial
# ---------------------------------------------------------------------------


def test_con_buzon_configurado_se_avisa_por_correo(settings, django_capture_on_commit_callbacks):
    settings.LEADS_NOTIFY_EMAIL = "comercial@ejemplo.es"

    # El aviso sale al confirmar la transaccion, no antes.
    with django_capture_on_commit_callbacks(execute=True):
        register_lead(name="Marta Gil", company="Gil Rent a Car", phone="600111222")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["comercial@ejemplo.es"]
    assert "Gil Rent a Car" in mail.outbox[0].subject
    assert "600111222" in mail.outbox[0].body


def test_sin_buzon_el_contacto_se_guarda_igual(settings):
    """El correo llegara mas adelante: eso no puede bloquear la captacion."""
    settings.LEADS_NOTIFY_EMAIL = ""

    lead = register_lead(name="Marta Gil", phone="600111222")

    assert lead.pk is not None
    assert not mail.outbox


def test_si_el_correo_falla_el_contacto_sigue_guardado(
    settings, monkeypatch, django_capture_on_commit_callbacks
):
    settings.LEADS_NOTIFY_EMAIL = "comercial@ejemplo.es"

    def explota(*args, **kwargs):
        raise OSError("buzon caido")

    monkeypatch.setattr("apps.core.leads.send_mail", explota)

    with django_capture_on_commit_callbacks(execute=True):
        lead = register_lead(name="Marta Gil", phone="600111222")

    assert Lead.objects.filter(pk=lead.pk).exists()


# ---------------------------------------------------------------------------
# La portada ofrece contacto de verdad
# ---------------------------------------------------------------------------


def test_la_portada_ofrece_whatsapp_y_formulario(client, settings):
    settings.CONTACT_WHATSAPP = "683472110"

    contenido = client.get(reverse("core:home")).content.decode()

    assert "https://wa.me/34683472110" in contenido
    assert reverse("core:lead_create") in contenido
    assert "Quiero que me llaméis" in contenido


def test_el_correo_solo_sale_cuando_esta_configurado(client, settings):
    settings.CONTACT_EMAIL = ""
    assert "mailto:" not in client.get(reverse("core:home")).content.decode()

    settings.CONTACT_EMAIL = "hola@ejemplo.es"
    assert "mailto:hola@ejemplo.es" in client.get(reverse("core:home")).content.decode()


# ---------------------------------------------------------------------------
# Pantalla interna
# ---------------------------------------------------------------------------


def test_el_listado_pide_permiso(client, agente_centro):
    """El mostrador no gestiona contactos comerciales."""
    client.force_login(agente_centro)

    assert client.get(reverse("core:lead_list")).status_code == 403


def test_administracion_ve_y_sigue_los_contactos(client, db):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    lead = register_lead(name="Marta Gil", company="Gil Rent a Car", phone="600111222")
    usuario = UserFactory(
        email="admin-contactos@ejemplo.es",
        role=RoleFactory(
            code="admin-contactos",
            name="Administracion",
            permissions=["core.view_lead", "core.change_lead"],
        ),
    )
    client.force_login(usuario)

    listado = client.get(reverse("core:lead_list"))
    assert listado.status_code == 200
    assert "Gil Rent a Car" in listado.content.decode()

    respuesta = client.post(
        reverse("core:lead_update", args=[lead.pk]),
        {"status": LeadStatus.CONTACTED, "notes": "Llamada el lunes."},
    )

    assert respuesta.status_code in (200, 302)
    lead.refresh_from_db()
    assert lead.status == LeadStatus.CONTACTED
    assert lead.notes == "Llamada el lunes."
