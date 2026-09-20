"""Arqueo de caja y pantallas de cobros."""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Payment, PaymentMethod, PaymentType
from apps.billing.selectors import cash_register, cash_totals_by_method
from apps.billing.services import register_payment

pytestmark = pytest.mark.django_db


def _cobrar(reserva, importe, actor, metodo=PaymentMethod.CASH, **kwargs):
    return register_payment(
        reservation=reserva, amount=importe, method=metodo, actor=actor, **kwargs
    )


# ---------------------------------------------------------------------------
# Arqueo
# ---------------------------------------------------------------------------


def test_el_arqueo_suma_solo_el_efectivo(reserva, cajero, centro):
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH)
    _cobrar(reserva, Decimal("60.00"), cajero, PaymentMethod.CARD)
    _cobrar(reserva, Decimal("20.00"), cajero, PaymentMethod.CASH)

    arqueo = cash_register(office=centro, day=timezone.localdate())

    assert arqueo.count == 2
    assert arqueo.cash_in == Decimal("70.00")
    assert arqueo.cash_balance == Decimal("70.00")


def test_el_arqueo_separa_entradas_de_salidas(reserva, cajero, centro):
    cobro = _cobrar(reserva, Decimal("100.00"), cajero, PaymentMethod.CASH)
    from apps.billing.services import refund

    refund(payment=cobro, amount=Decimal("40.00"), actor=cajero)

    arqueo = cash_register(office=centro, day=timezone.localdate())

    assert arqueo.cash_in == Decimal("100.00")
    assert arqueo.cash_out == Decimal("-40.00")
    assert arqueo.cash_balance == Decimal("60.00")


def test_el_arqueo_no_mezcla_oficinas(reserva, cajero, centro, norte):
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH, office=centro)
    _cobrar(reserva, Decimal("30.00"), cajero, PaymentMethod.CASH, office=norte)

    assert cash_register(office=centro, day=timezone.localdate()).cash_in == Decimal("50.00")
    assert cash_register(office=norte, day=timezone.localdate()).cash_in == Decimal("30.00")


def test_el_arqueo_no_mezcla_dias(reserva, cajero, centro):
    from datetime import timedelta

    ayer = timezone.now() - timedelta(days=1)
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH)
    _cobrar(reserva, Decimal("30.00"), cajero, PaymentMethod.CASH, paid_at=ayer)

    hoy = cash_register(office=centro, day=timezone.localdate())

    assert hoy.cash_in == Decimal("50.00")
    assert cash_register(office=centro, day=ayer.date()).cash_in == Decimal("30.00")


def test_la_fianza_en_efectivo_si_entra_en_caja(reserva, cajero, centro):
    """No cuenta como cobro del alquiler, pero el billete esta en el cajon."""
    _cobrar(
        reserva, Decimal("150.00"), cajero, PaymentMethod.CASH, payment_type=PaymentType.DEPOSIT
    )

    arqueo = cash_register(office=centro, day=timezone.localdate())

    assert arqueo.cash_in == Decimal("150.00")


def test_los_totales_por_metodo_cuadran_con_el_datafono(reserva, cajero, centro):
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH)
    _cobrar(reserva, Decimal("60.00"), cajero, PaymentMethod.CARD)
    _cobrar(reserva, Decimal("20.00"), cajero, PaymentMethod.CARD)

    totales = dict(cash_totals_by_method(office=centro, day=timezone.localdate()))

    assert totales["Efectivo"] == Decimal("50.00")
    assert totales["Tarjeta"] == Decimal("80.00")
    assert "Transferencia" not in totales


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------


def test_la_pantalla_de_arqueo_pide_permiso(client, reserva, centro, db):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    pelado = UserFactory(
        email="pelado@ejemplo.es",
        role=RoleFactory(code="pelado-caja", name="Sin permisos"),
        offices=[centro],
    )
    client.force_login(pelado)

    assert client.get(reverse("billing:cash_register")).status_code == 403


def test_la_pantalla_de_arqueo_se_ve(client, reserva, cajero, centro):
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH)
    client.force_login(cajero)

    respuesta = client.get(
        reverse("billing:cash_register"),
        {"office": centro.pk, "day": timezone.localdate().isoformat()},
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Deberia haber en caja" in contenido
    assert reserva.number in contenido


def test_registrar_un_cobro_desde_la_ficha(client, reserva, cajero):
    client.force_login(cajero)

    respuesta = client.post(
        reverse("billing:payment_create", args=[reserva.pk]),
        {
            "amount": "50.00",
            "method": PaymentMethod.CARD,
            "payment_type": PaymentType.PAYMENT,
            "reference": "OP-123",
            "notes": "",
        },
    )

    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    pago = Payment.objects.get()
    assert pago.amount == Decimal("50.00")
    assert pago.created_by == cajero
    assert pago.office == reserva.pickup_office


def test_cobrar_de_mas_desde_la_ficha_avisa(client, reserva, cajero):
    client.force_login(cajero)

    respuesta = client.post(
        reverse("billing:payment_create", args=[reserva.pk]),
        {"amount": "500.00", "method": PaymentMethod.CASH, "payment_type": PaymentType.PAYMENT},
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 422
    assert "pasa del pendiente" in contenido
    # El cajero no puede autorizarlo: no se le ofrece el boton.
    assert "de todos modos" not in contenido
    assert not Payment.objects.exists()


def test_el_responsable_si_ve_el_boton_de_autorizar(client, reserva, responsable):
    client.force_login(responsable)

    respuesta = client.post(
        reverse("billing:payment_create", args=[reserva.pk]),
        {"amount": "500.00", "method": PaymentMethod.CASH, "payment_type": PaymentType.PAYMENT},
    )

    assert respuesta.status_code == 422
    assert "de todos modos" in respuesta.content.decode()


def test_la_pestana_de_cobros_ensena_los_cuatro_numeros(client, reserva, cajero):
    _cobrar(reserva, Decimal("50.00"), cajero, PaymentMethod.CASH)
    _cobrar(
        reserva, Decimal("150.00"), cajero, PaymentMethod.CASH, payment_type=PaymentType.DEPOSIT
    )
    client.force_login(cajero)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "cobros"])
    ).content.decode()

    assert "Cobrado" in contenido
    assert "Fianza retenida" in contenido
    assert "No es un cobro del alquiler" in contenido
    assert "113,35" in contenido  # pendiente: 163,35 - 50


def test_la_cabecera_ensena_la_fianza(client, reserva, cajero):
    _cobrar(
        reserva, Decimal("150.00"), cajero, PaymentMethod.CASH, payment_type=PaymentType.DEPOSIT
    )
    client.force_login(cajero)

    contenido = client.get(reverse("reservations:header", args=[reserva.pk])).content.decode()

    assert "Fianza retenida" in contenido
    assert "150,00" in contenido
