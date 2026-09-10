"""Reglas de la reserva. Las vistas orquestan, aqui se decide.

El estado **no** se toca desde aqui: para eso esta `state_machine.transition()`.
Lo que vive en este modulo es el alta, el precio congelado y los apuntes que
dejan el check-in y el check-out.
"""

from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.availability.services import reserve_capacity
from apps.core.services import ServiceError
from apps.pricing.dto import PriceBreakdown, PriceQuoteInput
from apps.pricing.models import Channel
from apps.pricing.services import calculate_reservation_price

from .models import (
    CancellationPolicy,
    FuelPolicy,
    Reservation,
    ReservationExtra,
    ReservationStatus,
    ReservationStatusChange,
)

logger = structlog.get_logger(__name__)


class ReservationServiceError(ServiceError):
    """Regla de negocio incumplida sobre una reserva."""


# ---------------------------------------------------------------------------
# Precio
# ---------------------------------------------------------------------------


def quote(
    *,
    category,
    pickup_office,
    return_office=None,
    pickup_at,
    return_at,
    extras=(),
    channel: str = Channel.COUNTER,
    rate=None,
    customer=None,
    manual_override: Decimal | None = None,
    discount_code: str = "",
) -> PriceBreakdown:
    """Precio de un alquiler que todavia no existe.

    Es lo que consulta el mostrador mientras teclea. No toca la base de datos de
    reservas ni crea nada.
    """
    return calculate_reservation_price(
        PriceQuoteInput(
            category=category,
            pickup_office=pickup_office,
            return_office=return_office or pickup_office,
            pickup_at=pickup_at,
            return_at=return_at,
            extras=tuple(extras),
            channel=channel,
            rate=rate,
            customer_age=_edad(customer, pickup_at),
            manual_override=manual_override,
            discount_code=discount_code,
        )
    )


def _edad(customer, en_fecha) -> int | None:
    nacimiento = getattr(customer, "birth_date", None)
    if nacimiento is None:
        return None
    fecha = timezone.localtime(en_fecha).date() if timezone.is_aware(en_fecha) else en_fecha.date()
    return (
        fecha.year
        - nacimiento.year
        - ((fecha.month, fecha.day) < (nacimiento.month, nacimiento.day))
    )


def _campos_de_precio(breakdown: PriceBreakdown) -> dict:
    """Copia del calculo que se guarda en la reserva.

    Se guarda el resultado, no la referencia a las tarifas: el desglose de una
    reserva de hace un ano tiene que leerse tal como se vendio.
    """
    return {
        "rate": breakdown.applied_rate,
        "base_amount": breakdown.base_amount,
        "extras_total": breakdown.extras_total,
        "supplements_total": breakdown.supplements_total,
        "discounts_total": breakdown.discounts_total,
        "tax_total": breakdown.tax_total,
        "total": breakdown.total,
        "price_breakdown": breakdown.to_dict(),
    }


def _congelar_extras(reservation: Reservation, breakdown: PriceBreakdown, extras) -> None:
    """Copia las lineas de extras a la reserva con su precio del dia.

    A partir de aqui, la reserva no vuelve a mirar el maestro de extras para
    saber cuanto cobro.
    """
    lineas = {linea.source_code: linea for linea in breakdown.lines_of("extra")}
    for peticion in extras:
        linea = lineas.get(peticion.extra.code)
        if linea is None:
            # El motor no la cobro (tope, no aplicable...): no se guarda linea.
            continue
        ReservationExtra.objects.create(
            reservation=reservation,
            extra=peticion.extra,
            concept=linea.concept or peticion.extra.name,
            quantity=peticion.quantity,
            unit_price=linea.unit_price,
            tax_rate=linea.tax_rate,
            base_amount=linea.base,
            tax_amount=linea.tax_amount,
            total=linea.total,
        )


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


@transaction.atomic
def create_quick_reservation(
    *,
    category,
    pickup_office,
    pickup_at,
    return_at,
    customer,
    return_office=None,
    extras=(),
    channel: str = Channel.COUNTER,
    fuel_policy: str = FuelPolicy.FULL_FULL,
    included_km: int | None = None,
    cancellation_policy: str = CancellationPolicy.FLEXIBLE,
    notes: str = "",
    internal_notes: str = "",
    vehicle=None,
    override=None,
    actor=None,
) -> Reservation:
    """Alta rapida de mostrador: nace ya PENDIENTE y con precio cerrado.

    Todo ocurre dentro de una transaccion: se calcula el precio, se recuenta la
    disponibilidad con el grupo bloqueado y se crea la reserva. Si no hay hueco,
    no se ha escrito nada.
    """
    if customer is None:
        raise ReservationServiceError(_("Una reserva de mostrador necesita cliente."))
    if getattr(customer, "is_blacklisted", False):
        raise ReservationServiceError(
            _("%(cliente)s esta marcado y no se le puede alquilar.")
            % {"cliente": customer.full_name if hasattr(customer, "full_name") else customer}
        )

    extras = tuple(extras)
    breakdown = quote(
        category=category,
        pickup_office=pickup_office,
        return_office=return_office,
        pickup_at=pickup_at,
        return_at=return_at,
        extras=extras,
        channel=channel,
        customer=customer,
    )

    reservation = reserve_capacity(
        category=category,
        pickup_office=pickup_office,
        return_office=return_office,
        start=pickup_at,
        end=return_at,
        vehicle=vehicle,
        status=ReservationStatus.PENDING,
        override=override,
        actor=actor,
        customer=customer,
        channel=channel,
        fuel_policy=fuel_policy,
        included_km=included_km,
        cancellation_policy=cancellation_policy,
        notes=notes,
        internal_notes=internal_notes,
        **_campos_de_precio(breakdown),
    )

    _congelar_extras(reservation, breakdown, extras)

    # El alta deja su rastro en el historico como cualquier otro cambio: la
    # reserva nace y se queda pendiente en el mismo gesto.
    ReservationStatusChange.objects.create(
        reservation=reservation,
        from_status=ReservationStatus.DRAFT,
        to_status=ReservationStatus.PENDING,
        reason=str(_("Alta rapida de mostrador")),
        changed_by=actor if getattr(actor, "pk", None) else None,
    )

    logger.info(
        "reserva_alta_rapida",
        reservation_number=reservation.number,
        customer_id=customer.pk,
        total=str(reservation.total),
        extras=[e.extra.code for e in extras],
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


# ---------------------------------------------------------------------------
# Apuntes de mostrador
# ---------------------------------------------------------------------------


@transaction.atomic
def record_pickup(*, reservation: Reservation, at=None, actor=None) -> Reservation:
    """Deja constancia de la hora real de recogida.

    Provisional: cuando exista `operations`, quien escribe esto es el check-in.
    La precondicion de la maquina de estados no cambia.
    """
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    reservation.actual_pickup_at = at or timezone.now()
    reservation.save(update_fields=["actual_pickup_at", "updated_at"])
    logger.info(
        "recogida_registrada",
        reservation_number=reservation.number,
        actual_pickup_at=reservation.actual_pickup_at.isoformat(),
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


@transaction.atomic
def record_return(*, reservation: Reservation, at=None, actor=None) -> Reservation:
    """Deja constancia de la hora real de devolucion (futuro check-out)."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    reservation.actual_return_at = at or timezone.now()
    reservation.save(update_fields=["actual_return_at", "updated_at"])
    logger.info(
        "devolucion_registrada",
        reservation_number=reservation.number,
        actual_return_at=reservation.actual_return_at.isoformat(),
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


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
        reservas=[r.number for r in afectadas],
        actor_id=getattr(actor, "pk", None),
    )
    for reserva in afectadas:
        reserva.refresh_from_db()
    return afectadas
