"""Maquina de estados de la reserva.

Un unico sitio decide que transiciones existen, quien puede hacerlas, que tiene
que cumplirse antes y que pasa despues. Fuera de aqui **nadie** escribe
`reservation.status = X`: ni una vista, ni un formulario, ni el admin.

    BORRADOR -> PENDIENTE -> CONFIRMADA -> EN_CURSO -> FINALIZADA
                          -> CANCELADA
             CONFIRMADA   -> NO_SHOW

Cada transicion declara:

* `permission`: permiso Django exigido, o vacio si basta con estar dentro.
* `preconditions`: comprobaciones sobre la reserva. Devuelven un mensaje si no
  se cumplen, o None si todo esta en orden.
* `effects`: lo que hay que hacer despues de cambiar el estado, en orden.
* `requires_reason`: la transicion no se acepta sin un motivo escrito.

Toda la operacion va dentro de una transaccion: si un efecto falla, ni el
estado cambia ni queda rastro en el historico.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import structlog
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import (
    CancellationPolicy,
    Reservation,
    ReservationStatus,
    ReservationStatusChange,
)

logger = structlog.get_logger(__name__)


class InvalidTransition(ServiceError):
    """Ese salto no existe en el diagrama."""


class TransitionRefused(ServiceError):
    """La transicion existe, pero la reserva no esta en condiciones."""


# ---------------------------------------------------------------------------
# Precondiciones
# ---------------------------------------------------------------------------
# Devuelven None si se cumplen y un mensaje si no. Reciben la reserva.


def requiere_cliente(reservation: Reservation):
    if reservation.customer_id is None:
        return _("La reserva no tiene cliente. Sin cliente no se puede confirmar.")
    return None


def requiere_vehiculo(reservation: Reservation):
    if reservation.vehicle_id is None:
        return _("Hay que asignar un vehiculo antes de entregar el coche.")
    return None


def requiere_check_in(reservation: Reservation):
    """El check-in deja escrita la hora real de recogida.

    Mientras `operations` no exista, quien la escribe es `record_pickup()`. La
    precondicion no cambia cuando llegue el check-in de verdad.
    """
    if reservation.actual_pickup_at is None:
        return _("Falta el check-in: no consta la hora real de recogida.")
    return None


def requiere_check_out(reservation: Reservation):
    if reservation.actual_return_at is None:
        return _("Falta el check-out: no consta la hora real de devolucion.")
    return None


def requiere_precio(reservation: Reservation):
    if reservation.total <= Decimal("0.00"):
        return _("La reserva no tiene precio calculado.")
    return None


def vehiculo_disponible(reservation: Reservation):
    """El coche asignado sigue siendo utilizable el dia de la entrega."""
    if reservation.needs_reassignment:
        return _("El vehiculo asignado salio de flota. Asigna otro antes de entregar.")
    return None


# ---------------------------------------------------------------------------
# Efectos
# ---------------------------------------------------------------------------
# Reciben (reservation, user, reason). Se ejecutan ya dentro de la transaccion.


def marcar_recogida_real(reservation: Reservation, user, reason):
    """Si el check-in no dejo hora, se usa la de ahora."""
    if reservation.actual_pickup_at is None:
        reservation.actual_pickup_at = timezone.now()
        reservation.save(update_fields=["actual_pickup_at", "updated_at"])


def poner_vehiculo_alquilado(reservation: Reservation, user, reason):
    from apps.fleet.services import start_rental

    if reservation.vehicle_id:
        start_rental(vehicle=reservation.vehicle, actor=user)


def devolver_vehiculo_a_flota(reservation: Reservation, user, reason):
    from apps.fleet.services import finish_rental

    if reservation.vehicle_id:
        finish_rental(vehicle=reservation.vehicle, actor=user)


def aplicar_politica_de_cancelacion(reservation: Reservation, user, reason):
    """Calcula y congela el cargo por cancelar.

    Se guarda el importe, no la regla: si manana cambia la politica comercial,
    lo cobrado ayer no se mueve.
    """
    reservation.cancellation_fee = cancellation_fee_for(reservation)
    reservation.save(update_fields=["cancellation_fee", "updated_at"])


def liberar_vehiculo(reservation: Reservation, user, reason):
    """Suelta el coche al cancelar, salvo que ya estuviera fuera.

    Una cancelacion en curso significa que el coche sigue con el cliente: el
    estado del vehiculo lo arreglara la devolucion, no esta transicion.
    """
    from apps.fleet.models import VehicleStatus
    from apps.fleet.services import set_vehicle_status

    if not reservation.vehicle_id:
        return
    if reservation.actual_pickup_at is not None:
        logger.warning(
            "cancelacion_con_coche_fuera",
            reservation_number=reservation.number,
            vehicle_id=reservation.vehicle_id,
        )
        return
    if reservation.vehicle.status == VehicleStatus.RESERVED:
        set_vehicle_status(vehicle=reservation.vehicle, status=VehicleStatus.AVAILABLE, actor=user)


# ---------------------------------------------------------------------------
# Politica de cancelacion
# ---------------------------------------------------------------------------

#: Horas de antelacion a partir de las cuales cancelar no cuesta nada.
HORAS_SIN_CARGO = {
    CancellationPolicy.FLEXIBLE: 24,
    CancellationPolicy.MODERATE: 72,
}


def cancellation_fee_for(reservation: Reservation, *, at=None) -> Decimal:
    """Cuanto se cobra por cancelar esta reserva ahora mismo.

    Funcion pura salvo por la hora: se puede probar sin base de datos.
    """
    ahora = at or timezone.now()
    politica = reservation.cancellation_policy

    if politica == CancellationPolicy.NON_REFUNDABLE:
        return reservation.total

    if reservation.actual_pickup_at is not None:
        # El coche ya salio: se cobra lo consumido, que como minimo es un dia.
        return _importe_de_un_dia(reservation)

    horas = (reservation.pickup_at - ahora).total_seconds() / 3600

    if politica == CancellationPolicy.STRICT:
        return Decimal("0.00") if horas >= 24 * 7 else _importe_de_un_dia(reservation)

    margen = HORAS_SIN_CARGO.get(politica, 24)
    return Decimal("0.00") if horas >= margen else _importe_de_un_dia(reservation)


def _importe_de_un_dia(reservation: Reservation) -> Decimal:
    dias = reservation.price_breakdown.get("rental_days") or 1
    try:
        return (reservation.base_amount / Decimal(dias)).quantize(Decimal("0.01"))
    except (ZeroDivisionError, ArithmeticError):  # pragma: no cover - dias siempre >= 1
        return reservation.base_amount


# ---------------------------------------------------------------------------
# El diagrama
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    to: str
    label: str
    permission: str = ""
    preconditions: tuple[Callable, ...] = ()
    effects: tuple[Callable, ...] = ()
    requires_reason: bool = False
    #: Texto para el boton de confirmacion de acciones delicadas.
    confirm: str = ""


TRANSITIONS: dict[str, dict[str, Transition]] = {
    ReservationStatus.DRAFT: {
        ReservationStatus.PENDING: Transition(
            to=ReservationStatus.PENDING,
            label=_("Pasar a pendiente"),
            permission="reservations.change_reservation",
            preconditions=(requiere_cliente, requiere_precio),
        ),
        ReservationStatus.CANCELLED: Transition(
            to=ReservationStatus.CANCELLED,
            label=_("Descartar borrador"),
            permission="reservations.cancel_reservation",
            effects=(aplicar_politica_de_cancelacion,),
        ),
    },
    ReservationStatus.PENDING: {
        ReservationStatus.CONFIRMED: Transition(
            to=ReservationStatus.CONFIRMED,
            label=_("Confirmar"),
            permission="reservations.change_reservation",
            preconditions=(requiere_cliente, requiere_precio),
        ),
        ReservationStatus.CANCELLED: Transition(
            to=ReservationStatus.CANCELLED,
            label=_("Cancelar"),
            permission="reservations.cancel_reservation",
            effects=(aplicar_politica_de_cancelacion, liberar_vehiculo),
            confirm=_("Se cancelara la reserva y se liberara el hueco."),
        ),
    },
    ReservationStatus.CONFIRMED: {
        ReservationStatus.IN_PROGRESS: Transition(
            to=ReservationStatus.IN_PROGRESS,
            label=_("Entregar el coche"),
            permission="reservations.change_reservation",
            preconditions=(requiere_vehiculo, vehiculo_disponible, requiere_check_in),
            effects=(marcar_recogida_real, poner_vehiculo_alquilado),
        ),
        ReservationStatus.CANCELLED: Transition(
            to=ReservationStatus.CANCELLED,
            label=_("Cancelar"),
            permission="reservations.cancel_reservation",
            effects=(aplicar_politica_de_cancelacion, liberar_vehiculo),
            confirm=_("Se cancelara la reserva y se liberara el hueco."),
        ),
        ReservationStatus.NO_SHOW: Transition(
            to=ReservationStatus.NO_SHOW,
            label=_("Marcar como no show"),
            permission="reservations.cancel_reservation",
            requires_reason=True,
            effects=(liberar_vehiculo,),
            confirm=_("El cliente no se ha presentado. El hueco queda libre."),
        ),
    },
    ReservationStatus.IN_PROGRESS: {
        ReservationStatus.FINISHED: Transition(
            to=ReservationStatus.FINISHED,
            label=_("Finalizar"),
            permission="reservations.change_reservation",
            preconditions=(requiere_check_out,),
            effects=(devolver_vehiculo_a_flota,),
        ),
        # El coche esta fuera: cancelar aqui es una operacion excepcional, y por
        # eso pide permiso de cancelacion y motivo escrito siempre.
        ReservationStatus.CANCELLED: Transition(
            to=ReservationStatus.CANCELLED,
            label=_("Cancelar con el coche fuera"),
            permission="reservations.cancel_reservation",
            requires_reason=True,
            effects=(aplicar_politica_de_cancelacion,),
            confirm=_(
                "El coche esta con el cliente. Cancelar aqui no lo devuelve a flota: "
                "hay que registrar la devolucion aparte."
            ),
        ),
    },
    # Estados finales: de aqui no se sale.
    ReservationStatus.FINISHED: {},
    ReservationStatus.CANCELLED: {},
    ReservationStatus.NO_SHOW: {},
}


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def allowed_targets(from_status: str) -> dict[str, Transition]:
    return TRANSITIONS.get(from_status, {})


def can_transition(reservation: Reservation, to_status: str, user) -> bool:
    """Solo para pintar botones. Nunca sustituye a `transition()`."""
    salto = allowed_targets(reservation.status).get(to_status)
    if salto is None:
        return False
    if salto.permission and not user.has_perm(salto.permission):
        return False
    return all(comprobar(reservation) is None for comprobar in salto.preconditions)


def available_transitions(reservation: Reservation, user) -> list[Transition]:
    """Transiciones que este usuario puede hacer ahora mismo."""
    return [
        salto
        for salto in allowed_targets(reservation.status).values()
        if not salto.permission or user.has_perm(salto.permission)
    ]


@transaction.atomic
def transition(
    reservation: Reservation,
    to_status: str,
    user,
    reason: str | None = None,
) -> Reservation:
    """Unica puerta para cambiar el estado de una reserva.

    Comprueba en este orden: que el salto exista, que el usuario pueda, que la
    reserva este en condiciones y que haya motivo si hace falta. Solo entonces
    escribe, y deja el rastro en el historico.
    """
    # Se recarga con bloqueo: entre que la pantalla se pinto y llega el POST,
    # otro pudo mover la reserva.
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    desde = reservation.status

    salto = allowed_targets(desde).get(to_status)
    if salto is None:
        raise InvalidTransition(
            _("No se puede pasar de %(desde)s a %(hasta)s.")
            % {
                "desde": ReservationStatus(desde).label,
                "hasta": ReservationStatus(to_status).label
                if to_status in ReservationStatus.values
                else to_status,
            }
        )

    if salto.permission and not user.has_perm(salto.permission):
        # PermissionDenied y no ServiceError: esto es un 403, no un aviso.
        logger.warning(
            "transicion_sin_permiso",
            reservation_number=reservation.number,
            desde=desde,
            hasta=to_status,
            permiso=salto.permission,
            user_id=getattr(user, "pk", None),
        )
        raise PermissionDenied(
            _("Tu usuario no puede hacer esta operacion (%(permiso)s).")
            % {"permiso": salto.permission}
        )

    motivo = (reason or "").strip()
    if salto.requires_reason and not motivo:
        raise TransitionRefused(_("Esta operacion no se puede hacer sin escribir un motivo."))

    for comprobar in salto.preconditions:
        problema = comprobar(reservation)
        if problema is not None:
            raise TransitionRefused(problema)

    reservation.status = to_status
    reservation.save(update_fields=["status", "updated_at"])

    for efecto in salto.effects:
        efecto(reservation, user, motivo)

    ReservationStatusChange.objects.create(
        reservation=reservation,
        from_status=desde,
        to_status=to_status,
        reason=motivo,
        changed_by=user if getattr(user, "pk", None) else None,
    )

    from apps.auditlog import services as audit
    from apps.auditlog.models import AuditAction

    audit.record(
        AuditAction.CANCEL
        if to_status in (ReservationStatus.CANCELLED, ReservationStatus.NO_SHOW)
        else AuditAction.STATUS,
        f"{reservation.number}: {ReservationStatus(desde).label} → "
        f"{ReservationStatus(to_status).label}",
        obj=reservation,
        actor=user,
        changes={"status": [desde, to_status], "reason": motivo},
    )
    # Correos al cliente: al confirmar y al terminar el alquiler.
    from apps.notifications.models import EmailKind
    from apps.notifications.services import queue_email

    if to_status == ReservationStatus.CONFIRMED:
        queue_email(kind=EmailKind.CONFIRMATION, reservation=reservation, actor=user)
    elif to_status == ReservationStatus.FINISHED:
        queue_email(kind=EmailKind.RETURN, reservation=reservation, actor=user)

    logger.info(
        "reserva_cambio_de_estado",
        reservation_number=reservation.number,
        desde=desde,
        hasta=to_status,
        motivo=motivo,
        libera_capacidad=reservation.status not in ("pending", "confirmed", "in_progress"),
        user_id=getattr(user, "pk", None),
    )
    reservation.refresh_from_db()
    return reservation
