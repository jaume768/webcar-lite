"""Cobros: saldos, fianza, reembolsos e inmutabilidad."""

from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from apps.billing.models import Payment, PaymentImmutable, PaymentMethod, PaymentType
from apps.billing.selectors import (
    deposit_held,
    overpaid_amount,
    paid_amount,
    pending_amount,
)
from apps.billing.services import (
    BillingServiceError,
    OverpaymentNotAllowed,
    refund,
    register_payment,
)

pytestmark = pytest.mark.django_db

TOTAL = Decimal("163.35")


def _cobrar(reserva, importe, actor, **kwargs):
    kwargs.setdefault("method", PaymentMethod.CARD)
    return register_payment(reservation=reserva, amount=importe, actor=actor, **kwargs)


# ---------------------------------------------------------------------------
# Saldos
# ---------------------------------------------------------------------------


def test_sin_cobros_el_pendiente_es_el_total(reserva):
    assert reserva.total == TOTAL
    assert paid_amount(reserva) == Decimal("0.00")
    assert pending_amount(reserva) == TOTAL


def test_la_suma_de_cobros_es_lo_cobrado(reserva, cajero):
    _cobrar(reserva, Decimal("50.00"), cajero, payment_type=PaymentType.ADVANCE)
    _cobrar(reserva, Decimal("60.00"), cajero)
    _cobrar(reserva, Decimal("53.35"), cajero)

    assert paid_amount(reserva) == Decimal("163.35")
    assert pending_amount(reserva) == Decimal("0.00")


def test_el_reembolso_resta_de_lo_cobrado(reserva, cajero):
    """Va en negativo: el cobro original no se toca."""
    cobro = _cobrar(reserva, Decimal("100.00"), cajero)

    devolucion = refund(payment=cobro, amount=Decimal("30.00"), actor=cajero)

    assert devolucion.amount == Decimal("-30.00")
    assert devolucion.payment_type == PaymentType.REFUND
    assert paid_amount(reserva) == Decimal("70.00")
    assert pending_amount(reserva) == Decimal("93.35")
    # El cobro original sigue ahi, intacto.
    cobro.refresh_from_db()
    assert cobro.amount == Decimal("100.00")
    assert Payment.objects.count() == 2


def test_el_pendiente_nunca_es_negativo(reserva, responsable):
    _cobrar(reserva, Decimal("200.00"), responsable, allow_overpayment=True)

    assert pending_amount(reserva) == Decimal("0.00")
    assert overpaid_amount(reserva) == Decimal("36.65")


def test_no_se_puede_devolver_mas_de_lo_cobrado(reserva, cajero):
    cobro = _cobrar(reserva, Decimal("50.00"), cajero)

    with pytest.raises(BillingServiceError):
        refund(payment=cobro, amount=Decimal("80.00"), actor=cajero)


# ---------------------------------------------------------------------------
# Fianza
# ---------------------------------------------------------------------------


def test_la_fianza_no_altera_el_pendiente(reserva, cajero):
    """Es dinero retenido, no cobrado: el alquiler sigue debiendose entero."""
    _cobrar(reserva, Decimal("150.00"), cajero, payment_type=PaymentType.DEPOSIT)

    assert deposit_held(reserva) == Decimal("150.00")
    assert paid_amount(reserva) == Decimal("0.00")
    assert pending_amount(reserva) == TOTAL


def test_devolver_la_fianza_la_descuenta_de_lo_retenido(reserva, cajero):
    _cobrar(reserva, Decimal("150.00"), cajero, payment_type=PaymentType.DEPOSIT)

    _cobrar(reserva, Decimal("150.00"), cajero, payment_type=PaymentType.DEPOSIT_RETURN)

    assert deposit_held(reserva) == Decimal("0.00")
    assert pending_amount(reserva) == TOTAL
    assert Payment.objects.count() == 2


def test_no_se_devuelve_mas_fianza_de_la_retenida(reserva, cajero):
    _cobrar(reserva, Decimal("150.00"), cajero, payment_type=PaymentType.DEPOSIT)

    with pytest.raises(BillingServiceError) as fallo:
        _cobrar(reserva, Decimal("200.00"), cajero, payment_type=PaymentType.DEPOSIT_RETURN)

    assert "solo hay 150" in str(fallo.value)


def test_la_fianza_no_dispara_el_aviso_de_sobrepago(reserva, cajero):
    """600 EUR de fianza sobre un alquiler de 163 no es cobrar de mas."""
    pago = _cobrar(reserva, Decimal("600.00"), cajero, payment_type=PaymentType.DEPOSIT)

    assert pago.pk is not None
    assert deposit_held(reserva) == Decimal("600.00")


# ---------------------------------------------------------------------------
# Cobrar de mas
# ---------------------------------------------------------------------------


def test_cobrar_mas_del_pendiente_pide_confirmacion(reserva, cajero):
    with pytest.raises(OverpaymentNotAllowed) as fallo:
        _cobrar(reserva, Decimal("200.00"), cajero)

    assert "pasa del pendiente" in str(fallo.value)
    assert not Payment.objects.exists()


def test_confirmar_no_basta_sin_permiso(reserva, cajero):
    """Criterio: cobro superior al pendiente sin permiso, rechazado."""
    assert not cajero.has_perm("billing.allow_overpayment")

    with pytest.raises(PermissionDenied):
        _cobrar(reserva, Decimal("200.00"), cajero, allow_overpayment=True)

    assert not Payment.objects.exists()


def test_con_permiso_y_confirmacion_pasa(reserva, responsable):
    pago = _cobrar(reserva, Decimal("200.00"), responsable, allow_overpayment=True)

    assert pago.amount == Decimal("200.00")
    assert overpaid_amount(reserva) == Decimal("36.65")


def test_cobrar_justo_el_pendiente_no_pide_nada(reserva, cajero):
    pago = _cobrar(reserva, TOTAL, cajero)

    assert pago.pk is not None
    assert pending_amount(reserva) == Decimal("0.00")


def test_el_segundo_cobro_se_mide_contra_el_pendiente_que_queda(reserva, cajero):
    _cobrar(reserva, Decimal("150.00"), cajero)

    with pytest.raises(OverpaymentNotAllowed):
        _cobrar(reserva, Decimal("20.00"), cajero)  # quedan 13,35

    assert Payment.objects.count() == 1


# ---------------------------------------------------------------------------
# Inmutabilidad
# ---------------------------------------------------------------------------


def test_un_cobro_no_se_edita(reserva, cajero):
    """Criterio: solo se corrige con un apunte contrario."""
    cobro = _cobrar(reserva, Decimal("50.00"), cajero)

    cobro.amount = Decimal("500.00")
    with pytest.raises(PaymentImmutable):
        cobro.save()

    cobro.refresh_from_db()
    assert cobro.amount == Decimal("50.00")


def test_un_cobro_no_se_borra(reserva, cajero):
    cobro = _cobrar(reserva, Decimal("50.00"), cajero)

    with pytest.raises(PaymentImmutable):
        cobro.delete()

    assert Payment.objects.filter(pk=cobro.pk).exists()


def test_el_signo_lo_manda_el_concepto(reserva, cajero):
    """En el mostrador se teclea 50 tanto para cobrar como para devolver."""
    cobro = _cobrar(reserva, Decimal("50.00"), cajero)
    assert cobro.amount == Decimal("50.00")

    devolucion = _cobrar(reserva, Decimal("30.00"), cajero, payment_type=PaymentType.REFUND)
    assert devolucion.amount == Decimal("-30.00")


def test_la_base_de_datos_rechaza_un_signo_incoherente(reserva, cajero):
    """Red de seguridad: un reembolso en positivo no puede existir."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(
            reservation=reserva,
            amount=Decimal("30.00"),
            method=PaymentMethod.CASH,
            payment_type=PaymentType.REFUND,
            office=reserva.pickup_office,
        )


def test_un_cobro_de_cero_no_es_un_cobro(reserva, cajero):
    with pytest.raises(BillingServiceError):
        _cobrar(reserva, Decimal("0.00"), cajero)
