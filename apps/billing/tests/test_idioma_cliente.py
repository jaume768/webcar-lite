"""Lo que recibe el cliente sale en su idioma; el panel sigue en espanol."""

import secrets
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.gateways import GatewayError
from apps.billing.models import OnlinePayment, OnlinePurpose
from apps.billing.services import issue_invoice

pytestmark = pytest.mark.django_db


def _en_idioma(reserva, idioma):
    reserva.customer.language = idioma
    reserva.customer.save(update_fields=["language"])
    return reserva


def _texto_pdf(contenido: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(contenido)).pages)


def _enlace(reserva, purpose=OnlinePurpose.PAYMENT) -> OnlinePayment:
    return OnlinePayment.objects.create(
        token=secrets.token_urlsafe(24),
        reservation=reserva,
        provider="stripe",
        purpose=purpose,
        amount=Decimal("50.00"),
        expires_at=timezone.now() + timedelta(hours=72),
    )


# ---------------------------------------------------------------------------
# Correos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("idioma", "asunto", "cuerpo"),
    [
        ("es", "confirmada", "Tu reserva está confirmada"),
        ("en", "confirmed", "Your booking is confirmed"),
        ("de", "bestätigt", "Ihre Buchung ist bestätigt"),
        ("fr", "confirmée", "Votre réservation est confirmée"),
    ],
)
def test_la_confirmacion_sale_en_el_idioma_del_cliente(reserva, idioma, asunto, cuerpo):
    from apps.notifications.models import EmailKind
    from apps.notifications.services import queue_email, render_email

    apunte = queue_email(kind=EmailKind.CONFIRMATION, reservation=_en_idioma(reserva, idioma))
    titulo, _texto, html, _adjuntos = render_email(apunte)

    assert apunte.language == idioma
    assert asunto in titulo
    assert cuerpo in html
    assert f'lang="{idioma}"' in html


def test_ningun_correo_se_queda_a_medias_en_espanol(reserva):
    """Cada tipo de correo, montado en ingles, sin restos de las plantillas."""
    from apps.notifications.models import EmailKind
    from apps.notifications.services import queue_email, render_email

    _en_idioma(reserva, "en")
    pago = _enlace(reserva)
    restos = ("Reserva ", "Recogida", "Hola ", "Tu ", "Gracias", "Pago ", "Fianza")
    for tipo in (
        EmailKind.CONFIRMATION,
        EmailKind.REMINDER,
        EmailKind.RETURN,
        EmailKind.PAYMENT_LINK,
        EmailKind.PAYMENT_RECEIVED,
    ):
        apunte = queue_email(kind=tipo, reservation=reserva, context={"pago_id": pago.pk})
        titulo, texto, _html, _adjuntos = render_email(apunte)
        for resto in restos:
            assert resto not in titulo, (tipo, titulo)
            assert resto not in texto, (tipo, resto, texto)


# ---------------------------------------------------------------------------
# Factura
# ---------------------------------------------------------------------------


def test_la_factura_se_imprime_en_el_idioma_del_cliente(finalizada, facturador):
    from apps.billing.pdf import render_invoice_pdf

    factura = issue_invoice(reservation=_en_idioma(finalizada, "de"), actor=facturador)
    texto = _texto_pdf(render_invoice_pdf(factura))

    # Las etiquetas van en mayusculas por CSS.
    assert "rechnungsdatum" in texto.lower()
    assert "Nettobetrag" in texto
    assert "Base imponible" not in texto
    # Los datos copiados al emitir no se traducen: son los de la factura.
    assert factura.number in texto
    assert "Alquileres Centro, S.L." in texto


def test_un_idioma_desconocido_cae_al_de_la_instalacion(finalizada, facturador):
    from apps.billing.pdf import document_language

    finalizada.customer.language = "xx"
    assert document_language(finalizada.customer) == "es"
    assert document_language(None) == "es"


# ---------------------------------------------------------------------------
# Pago online
# ---------------------------------------------------------------------------


def test_la_pagina_de_pago_sale_en_el_idioma_del_cliente(client, reserva):
    pago = _enlace(_en_idioma(reserva, "fr"), OnlinePurpose.DEPOSIT)

    respuesta = client.get(reverse("billing:pay", args=[pago.token]))

    contenido = respuesta.content.decode()
    assert respuesta.status_code == 200
    assert "Bloquer la caution sur ma carte" in contenido
    assert "Caution (pré-autorisation)" in contenido
    assert 'lang="fr"' in contenido


def test_el_panel_sigue_en_espanol_despues_de_una_pagina_de_cliente(client, reserva, cajero):
    """El override del cliente no se filtra a la siguiente peticion del mostrador."""
    pago = _enlace(_en_idioma(reserva, "en"))
    client.get(reverse("billing:pay", args=[pago.token]))

    client.force_login(cajero)
    respuesta = client.get(reverse("reservations:detail", args=[reserva.pk]))

    assert respuesta.status_code == 200
    assert 'lang="es"' in respuesta.content.decode()


def test_un_fallo_de_la_pasarela_no_ensena_detalles_tecnicos(client, reserva, monkeypatch):
    """Regresion: el cliente veia 'Stripe no esta configurado (STRIPE_SECRET_KEY)'."""
    from apps.billing.gateways import stripe as pasarela_stripe

    def sin_configurar(**kwargs):
        raise GatewayError("Stripe no esta configurado (STRIPE_SECRET_KEY).")

    monkeypatch.setattr(pasarela_stripe, "create_checkout", sin_configurar)
    pago = _enlace(_en_idioma(reserva, "en"))

    respuesta = client.post(reverse("billing:pay", args=[pago.token]))

    contenido = respuesta.content.decode()
    assert respuesta.status_code == 409
    assert "We could not connect to the bank" in contenido
    assert "STRIPE_SECRET_KEY" not in contenido
