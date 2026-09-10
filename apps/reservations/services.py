"""Reglas de la reserva, en su version minima.

La maquina de estados completa (que transicion permite cual) llega en su propio
prompt. Aqui esta lo justo para que la disponibilidad se pueda probar de
principio a fin: liberar el hueco al cancelar y no perder reservas cuando un
coche sale de la flota.
"""

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError

from .models import CAPACITY_CONSUMING_STATUSES, Reservation, ReservationStatus

logger = structlog.get_logger(__name__)


class ReservationServiceError(ServiceError):
    """Regla de negocio incumplida sobre una reserva."""


@transaction.atomic
def set_status(*, reservation: Reservation, status: str, actor=None) -> Reservation:
    """Cambia el estado y, con el, si la reserva sigue ocupando flota.

    Provisional: en el prompt de reservas esto pasa a `state_machine.py`, que
    ademas dira que transiciones son legales. Lo que ya es definitivo es que el
    estado no se toca desde una vista escribiendo `reservation.status = X`.
    """
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    anterior = reservation.status
    if anterior == status:
        return reservation

    reservation.status = status
    reservation.save(update_fields=["status", "updated_at"])

    logger.info(
        "reserva_cambio_de_estado",
        reservation_code=reservation.code,
        anterior=anterior,
        nuevo=status,
        libera_capacidad=(
            anterior in CAPACITY_CONSUMING_STATUSES and status not in CAPACITY_CONSUMING_STATUSES
        ),
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


@transaction.atomic
def cancel(*, reservation: Reservation, reason: str = "", actor=None) -> Reservation:
    """Cancela y libera el hueco.

    Una reserva cancelada deja de restar capacidad en el mismo instante: es lo
    que permite volver a vender ese coche.
    """
    if reservation.status == ReservationStatus.FINISHED:
        raise ReservationServiceError(_("Una reserva finalizada ya no se puede cancelar."))

    reserva = set_status(reservation=reservation, status=ReservationStatus.CANCELLED, actor=actor)
    logger.info("reserva_cancelada", reservation_code=reserva.code, motivo=reason)
    return reserva


@transaction.atomic
def mark_for_reassignment(*, vehicle, actor=None) -> list[Reservation]:
    """Suelta el coche de sus reservas vivas y las deja marcadas.

    Cuando un vehiculo sale de flota a mitad de temporada, las reservas que lo
    tenian asignado **no se cancelan**: el cliente sigue teniendo su reserva
    contra la categoria, y lo que hay que hacer es darle otro coche. Perderlas
    aqui seria perder ventas ya cerradas.

    Solo afecta a lo que aun no ha salido: una reserva en curso o ya terminada
    con ese coche es historia y se queda como esta.
    """
    afectadas = list(
        Reservation.objects.select_for_update()
        .filter(
            vehicle=vehicle,
            status__in=(ReservationStatus.PENDING, ReservationStatus.CONFIRMED),
            pickup_at__gte=timezone.now(),
        )
        .select_related("category")
    )
    if not afectadas:
        return []

    Reservation.objects.filter(pk__in=[r.pk for r in afectadas]).update(
        vehicle=None, needs_reassignment=True, updated_at=timezone.now()
    )

    logger.warning(
        "reservas_pendientes_de_reasignar",
        vehicle_id=vehicle.pk,
        plate=vehicle.plate,
        reservas=[r.code for r in afectadas],
        actor_id=getattr(actor, "pk", None),
    )
    for reserva in afectadas:
        reserva.refresh_from_db()
    return afectadas
