"""Reglas de los cobros. Las vistas orquestan, aqui se decide."""

from decimal import Decimal

import structlog
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import DEPOSIT_TYPES, OUTGOING_TYPES, Payment, PaymentType
from .selectors import deposit_held, pending_amount

logger = structlog.get_logger(__name__)

PERMISO_SOBREPAGO = "billing.allow_overpayment"


class BillingServiceError(ServiceError):
    """Regla de negocio incumplida al registrar un cobro."""


class OverpaymentNotAllowed(BillingServiceError):
    """El cobro pasa del pendiente y quien lo intenta no puede autorizarlo."""


def _normalizar_importe(amount: Decimal, payment_type: str) -> Decimal:
    """El signo lo manda el concepto, no quien teclea.

    En el mostrador se escribe "50" tanto para cobrar como para devolver; es el
    concepto elegido el que decide si eso entra o sale.
    """
    importe = abs(Decimal(amount))
    if importe == 0:
        raise BillingServiceError(_("Un cobro de cero euros no es un cobro."))
    return -importe if payment_type in OUTGOING_TYPES else importe


@transaction.atomic
def register_payment(
    *,
    reservation,
    amount: Decimal,
    method: str,
    payment_type: str = PaymentType.PAYMENT,
    office=None,
    paid_at=None,
    reference: str = "",
    notes: str = "",
    allow_overpayment: bool = False,
    actor=None,
) -> Payment:
    """Registra un movimiento de dinero de una reserva.

    Comprueba dos cosas antes de escribir: que el signo cuadre con el concepto
    y que un cobro del alquiler no se pase del pendiente sin que alguien con
    permiso lo autorice a proposito.
    """
    importe = _normalizar_importe(amount, payment_type)

    if payment_type == PaymentType.DEPOSIT_RETURN:
        retenido = deposit_held(reservation)
        if abs(importe) > retenido:
            raise BillingServiceError(
                _("No se pueden devolver %(importe)s EUR: solo hay %(retenido)s retenidos.")
                % {"importe": abs(importe), "retenido": retenido}
            )

    # La fianza no toca el saldo del alquiler, asi que no se compara con el
    # pendiente: es dinero retenido, no cobrado.
    if payment_type not in DEPOSIT_TYPES and importe > 0:
        pendiente = pending_amount(reservation)
        if importe > pendiente:
            if not allow_overpayment:
                raise OverpaymentNotAllowed(
                    _(
                        "El cobro de %(importe)s EUR pasa del pendiente (%(pendiente)s EUR). "
                        "Hace falta confirmarlo."
                    )
                    % {"importe": importe, "pendiente": pendiente}
                )
            if actor is None or not actor.has_perm(PERMISO_SOBREPAGO):
                raise PermissionDenied(
                    _("Tu usuario no puede cobrar por encima del pendiente (%(permiso)s).")
                    % {"permiso": PERMISO_SOBREPAGO}
                )
            logger.warning(
                "cobro_por_encima_del_pendiente",
                reservation_number=reservation.number,
                importe=str(importe),
                pendiente=str(pendiente),
                actor_id=actor.pk,
            )

    pago = Payment(
        reservation=reservation,
        amount=importe,
        method=method,
        payment_type=payment_type,
        office=office or reservation.pickup_office,
        paid_at=paid_at,
        reference=reference.strip(),
        notes=notes,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    if paid_at is None:
        # Deja que el modelo ponga la hora por defecto.
        pago.paid_at = Payment._meta.get_field("paid_at").get_default()
    pago.full_clean(exclude=["reservation", "office", "created_by"])
    pago.save()

    logger.info(
        "cobro_registrado",
        reservation_number=reservation.number,
        payment_id=pago.pk,
        importe=str(importe),
        metodo=method,
        concepto=payment_type,
        office_id=pago.office_id,
        actor_id=getattr(actor, "pk", None),
    )
    return pago


@transaction.atomic
def refund(*, payment: Payment, amount: Decimal | None = None, reason: str = "", actor=None):
    """Devuelve dinero de un cobro anterior con un apunte contrario.

    El cobro original no se toca: en caja tiene que seguir viendose lo que
    entro y, aparte, lo que salio.
    """
    if payment.amount <= 0:
        raise BillingServiceError(_("Ese apunte ya es una salida de dinero."))

    importe = abs(Decimal(amount)) if amount is not None else payment.amount
    if importe > payment.amount:
        raise BillingServiceError(
            _("No se puede devolver mas de lo cobrado (%(cobrado)s EUR).")
            % {"cobrado": payment.amount}
        )

    tipo = PaymentType.DEPOSIT_RETURN if payment.is_deposit else PaymentType.REFUND
    return register_payment(
        reservation=payment.reservation,
        amount=importe,
        method=payment.method,
        payment_type=tipo,
        office=payment.office,
        reference=payment.reference,
        notes=reason,
        actor=actor,
    )
