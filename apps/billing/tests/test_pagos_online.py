"""Pagos online por enlace: Stripe o Redsys, a eleccion del mostrador o de la web."""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.billing import online
from apps.billing.gateways import GatewayError
from apps.billing.gateways import redsys as pasarela_redsys
from apps.billing.gateways import stripe as pasarela_stripe
from apps.billing.models import OnlinePayment, OnlinePurpose, OnlineStatus, PaymentType

pytestmark = pytest.mark.django_db

SECRETO_REDSYS = "sq7HjrUOBfKmC576ILgskD5srU870gJ7"  # clave publica del entorno de pruebas
SECRETO_WEBHOOK = "whsec_prueba"


@pytest.fixture(autouse=True)
def pasarelas(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    settings.STRIPE_WEBHOOK_SECRET = SECRETO_WEBHOOK
    settings.REDSYS_MERCHANT_CODE = "999008881"
    settings.REDSYS_SECRET_KEY = SECRETO_REDSYS


@pytest.fixture
def cobrador(db, centro):
    return UserFactory(
        email="online@ejemplo.es",
        role=RoleFactory(
            code="online",
            name="Online",
            permissions=["billing.add_onlinepayment", "billing.change_onlinepayment"],
        ),
        offices=[centro],
    )


def _enlace(reserva, actor, **kwargs):
    kwargs.setdefault("provider", "redsys")
    kwargs.setdefault("purpose", OnlinePurpose.PAYMENT)
    kwargs.setdefault("amount", reserva.total)
    return online.create_link(reservation=reserva, actor=actor, **kwargs)


def _notificacion_redsys(pedido: str, respuesta: str = "0000") -> tuple[str, str]:
    parametros = pasarela_redsys.encode_parameters(
        {"Ds_Order": pedido, "Ds_Response": respuesta, "Ds_AuthorisationCode": "123456"}
    )
    return parametros, pasarela_redsys.sign(parametros, pedido, secret=SECRETO_REDSYS)


# ---------------------------------------------------------------------------
# Enlace
# ---------------------------------------------------------------------------


def test_sin_permiso_no_hay_enlace(reserva, cajero):
    with pytest.raises(PermissionDenied):
        _enlace(reserva, cajero)


def test_pasarela_sin_configurar(reserva, cobrador, settings):
    settings.STRIPE_SECRET_KEY = ""
    with pytest.raises(online.OnlinePaymentError):
        _enlace(reserva, cobrador, provider="stripe")


def test_no_se_pide_mas_de_lo_pendiente(reserva, cobrador):
    with pytest.raises(online.OnlinePaymentError):
        _enlace(reserva, cobrador, amount=reserva.total + Decimal("0.01"))


def test_el_enlace_caduca(reserva, cobrador):
    pago = _enlace(reserva, cobrador)
    OnlinePayment.objects.filter(pk=pago.pk).update(expires_at=timezone.now() - timedelta(hours=1))
    assert online.expire_old_links() == 1
    pago.refresh_from_db()
    assert pago.status == OnlineStatus.EXPIRED
    with pytest.raises(online.OnlinePaymentError):
        online.start_checkout(token=pago.token)


# ---------------------------------------------------------------------------
# Redsys
# ---------------------------------------------------------------------------


def test_redsys_formulario_firmado(reserva, cobrador):
    pago = _enlace(reserva, cobrador)

    destino = online.start_checkout(token=pago.token)

    campos = destino["form"]["fields"]
    pago.refresh_from_db()
    assert pago.status == OnlineStatus.PENDING
    assert len(pago.provider_ref) == 12
    datos = json.loads(__import__("base64").b64decode(campos["Ds_MerchantParameters"]))
    assert datos["DS_MERCHANT_AMOUNT"] == str(int(reserva.total * 100))
    assert campos["Ds_Signature"] == pasarela_redsys.sign(
        campos["Ds_MerchantParameters"], pago.provider_ref
    )


def test_redsys_notificacion_apunta_el_cobro_una_sola_vez(reserva, cobrador):
    pago = _enlace(reserva, cobrador)
    online.start_checkout(token=pago.token)
    pago.refresh_from_db()
    parametros, firma = _notificacion_redsys(pago.provider_ref)

    online.handle_redsys_notification(parametros, firma)
    online.handle_redsys_notification(parametros, firma)  # el banco reintenta

    pago.refresh_from_db()
    assert pago.status == OnlineStatus.PAID
    assert reserva.payments.count() == 1
    assert reserva.payments.get().amount == reserva.total


def test_redsys_firma_falsa(reserva, cobrador):
    pago = _enlace(reserva, cobrador)
    online.start_checkout(token=pago.token)
    pago.refresh_from_db()
    parametros, _firma = _notificacion_redsys(pago.provider_ref)

    with pytest.raises(GatewayError):
        online.handle_redsys_notification(parametros, "firma-inventada")
    assert not reserva.payments.exists()


def test_redsys_denegado(reserva, cobrador):
    pago = _enlace(reserva, cobrador)
    online.start_checkout(token=pago.token)
    pago.refresh_from_db()

    online.handle_redsys_notification(*_notificacion_redsys(pago.provider_ref, "0190"))

    pago.refresh_from_db()
    assert pago.status == OnlineStatus.FAILED
    assert not reserva.payments.exists()


def test_redsys_vista_de_notificacion_rechaza_firma_falsa(client, reserva, cobrador):
    pago = _enlace(reserva, cobrador)
    online.start_checkout(token=pago.token)
    pago.refresh_from_db()
    parametros, _firma = _notificacion_redsys(pago.provider_ref)

    respuesta = client.post(
        "/pagos/redsys/notificacion/",
        {"Ds_MerchantParameters": parametros, "Ds_Signature": "x"},
    )
    assert respuesta.status_code == 400


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


@pytest.fixture
def stripe_simulado(monkeypatch):
    llamadas = []

    def crear(**kwargs):
        llamadas.append(kwargs)
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(pasarela_stripe, "create_checkout", crear)
    return llamadas


def _evento(pago, tipo="checkout.session.completed", estado="paid"):
    return {
        "id": "evt_1",
        "type": tipo,
        "data": {
            "object": {
                "id": "cs_test_1",
                "status": "complete",
                "payment_status": estado,
                "payment_intent": "pi_1",
                "metadata": {"token": pago.token},
            }
        },
    }


def _firmar(cuerpo: bytes) -> str:
    marca = str(int(time.time()))
    firma = hmac.new(
        SECRETO_WEBHOOK.encode(), f"{marca}.".encode() + cuerpo, hashlib.sha256
    ).hexdigest()
    return f"t={marca},v1={firma}"


def test_stripe_redirige_a_checkout(reserva, cobrador, stripe_simulado):
    pago = _enlace(reserva, cobrador, provider="stripe")

    destino = online.start_checkout(token=pago.token)

    assert destino["redirect"].startswith("https://checkout.stripe.com/")
    assert stripe_simulado[0]["amount"] == reserva.total
    assert stripe_simulado[0]["hold"] is False


def test_stripe_webhook_firmado_apunta_el_cobro_una_vez(client, reserva, cobrador, stripe_simulado):
    pago = _enlace(reserva, cobrador, provider="stripe")
    online.start_checkout(token=pago.token)
    cuerpo = json.dumps(_evento(pago)).encode()

    for _intento in range(2):
        respuesta = client.post(
            "/pagos/stripe/webhook/",
            cuerpo,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=_firmar(cuerpo),
        )
        assert respuesta.status_code == 200

    pago.refresh_from_db()
    assert pago.status == OnlineStatus.PAID
    assert reserva.payments.count() == 1


def test_stripe_webhook_sin_firma_valida(client, reserva, cobrador, stripe_simulado):
    pago = _enlace(reserva, cobrador, provider="stripe")
    cuerpo = json.dumps(_evento(pago)).encode()

    respuesta = client.post(
        "/pagos/stripe/webhook/",
        cuerpo,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=falsa",
    )

    assert respuesta.status_code == 400
    assert not reserva.payments.exists()


def test_stripe_pago_asincrono_pendiente_no_se_apunta(reserva, cobrador, stripe_simulado):
    pago = _enlace(reserva, cobrador, provider="stripe")
    online.start_checkout(token=pago.token)

    online.handle_stripe_event(_evento(pago, estado="unpaid"))

    assert not reserva.payments.exists()


def test_stripe_fianza_retenida_y_liberada(reserva, cobrador, stripe_simulado, monkeypatch):
    liberadas = []
    monkeypatch.setattr(pasarela_stripe, "release", liberadas.append)
    pago = _enlace(reserva, cobrador, provider="stripe", purpose=OnlinePurpose.DEPOSIT, amount=300)
    online.start_checkout(token=pago.token)
    assert stripe_simulado[0]["hold"] is True

    online.handle_stripe_event(_evento(pago, estado="unpaid"))
    pago.refresh_from_db()
    assert pago.status == OnlineStatus.AUTHORIZED

    online.release_deposit(online_payment=pago, actor=cobrador)

    pago.refresh_from_db()
    assert pago.status == OnlineStatus.RELEASED
    assert liberadas == ["pi_1"]
    tipos = sorted(reserva.payments.values_list("payment_type", flat=True))
    assert tipos == sorted([PaymentType.DEPOSIT, PaymentType.DEPOSIT_RETURN])
