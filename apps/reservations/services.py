"""Reglas de la reserva. Las vistas orquestan, aqui se decide.

El estado **no** se toca desde aqui: para eso esta `state_machine.transition()`.
Lo que vive en este modulo es el alta, el precio congelado y los apuntes que
dejan el check-in y el check-out.
"""

from dataclasses import dataclass
from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.availability.services import (
    check_category_availability,
    reserve_capacity,
    update_reservation_period,
    vehicle_conflicts,
)
from apps.billing.selectors import has_issued_invoice
from apps.core.services import ServiceError
from apps.pricing.dto import ExtraRequest, PriceBreakdown, PriceQuoteInput
from apps.pricing.models import Channel
from apps.pricing.services import (
    InvalidRentalPeriod,
    PricingError,
    calculate_reservation_price,
)

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


# ---------------------------------------------------------------------------
# Cambios sobre una reserva ya creada
# ---------------------------------------------------------------------------


class InvoicedReservationError(ReservationServiceError):
    """La reserva ya tiene factura emitida y quien lo intenta no puede tocarla."""


PERMISO_FACTURADA = "reservations.change_invoiced_reservation"


def _comprobar_factura(reservation: Reservation, actor) -> None:
    """Una factura emitida es inmutable: lo facturado no se mueve.

    Quien tenga el permiso puede seguir adelante, y entonces le toca emitir la
    rectificativa. Sin permiso, se corta aqui.
    """
    if not has_issued_invoice(reservation):
        return
    if actor is not None and actor.has_perm(PERMISO_FACTURADA):
        logger.warning(
            "cambio_sobre_reserva_facturada",
            reservation_number=reservation.number,
            actor_id=actor.pk,
        )
        return
    raise InvoicedReservationError(
        _(
            "La reserva %(numero)s ya esta facturada. Modificarla obliga a emitir "
            "una rectificativa y tu usuario no tiene ese permiso."
        )
        % {"numero": reservation.number}
    )


@dataclass(frozen=True)
class ChangePreview:
    """Lo que pasaria si se confirma el cambio. No escribe nada."""

    pickup_at: object
    return_at: object
    category: object
    availability: object
    price: PriceBreakdown | None
    current_total: Decimal
    new_total: Decimal
    difference: Decimal
    releases_vehicle: bool = False
    vehicle_conflict: str = ""
    blocked_reason: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def is_cheaper(self) -> bool:
        return self.difference < 0

    @property
    def can_confirm(self) -> bool:
        return not self.blocked_reason and self.availability is not None


def _extras_como_peticion(reservation: Reservation):
    """Los extras ya vendidos, listos para volver a pasarlos por el motor."""
    return tuple(
        ExtraRequest(extra=linea.extra, quantity=linea.quantity)
        for linea in reservation.extras.select_related("extra")
    )


def preview_change(
    *,
    reservation: Reservation,
    pickup_at=None,
    return_at=None,
    category=None,
    actor=None,
) -> ChangePreview:
    """Que disponibilidad hay y cuanto costaria, antes de tocar nada.

    Es lo que se le ensena al cliente por encima del mostrador: "le cambio las
    fechas, pero son 40 EUR mas".
    """
    # Se relee: el objeto que llega puede venir de antes de un cambio (una
    # asignacion de coche, por ejemplo), y un preview sobre datos viejos
    # ensenaria al mostrador algo que no es.
    reservation = Reservation.objects.select_related("vehicle", "category", "customer").get(
        pk=reservation.pk
    )

    nueva_recogida = pickup_at or reservation.pickup_at
    nueva_devolucion = return_at or reservation.return_at
    nueva_categoria = category or reservation.category

    bloqueo = ""
    try:
        _comprobar_factura(reservation, actor)
    except InvoicedReservationError as exc:
        bloqueo = str(exc)

    disponibilidad = None
    conflicto = ""
    avisos = []

    if nueva_devolucion <= nueva_recogida:
        bloqueo = bloqueo or str(_("La devolucion tiene que ser posterior a la recogida."))
    else:
        disponibilidad = check_category_availability(
            nueva_categoria,
            reservation.pickup_office,
            nueva_recogida,
            nueva_devolucion,
            exclude_reservation=reservation,
            rotation_minutes=reservation.rotation_minutes,
        )
        if reservation.vehicle_id:
            reservas, bloqueos = vehicle_conflicts(
                reservation.vehicle,
                nueva_recogida,
                nueva_devolucion,
                reservation,
                rotation_minutes=reservation.rotation_minutes,
            )
            if reservas:
                conflicto = str(
                    _("%(matricula)s ya esta comprometido en la reserva %(numeros)s.")
                    % {
                        "matricula": reservation.vehicle.plate,
                        "numeros": ", ".join(r.number for r in reservas),
                    }
                )
            elif bloqueos:
                conflicto = str(
                    _("%(matricula)s tiene un bloqueo en ese periodo.")
                    % {"matricula": reservation.vehicle.plate}
                )

    # Un cambio de categoria deja sin sentido el coche ya asignado.
    libera = bool(reservation.vehicle_id and nueva_categoria.pk != reservation.vehicle.category_id)
    if libera:
        avisos.append(
            str(
                _("%(matricula)s no es de la categoria %(categoria)s: se soltara la asignacion.")
                % {"matricula": reservation.vehicle.plate, "categoria": nueva_categoria.name}
            )
        )

    precio = None
    nuevo_total = reservation.total
    try:
        precio = quote(
            category=nueva_categoria,
            pickup_office=reservation.pickup_office,
            return_office=reservation.return_office,
            pickup_at=nueva_recogida,
            return_at=nueva_devolucion,
            extras=_extras_como_peticion(reservation),
            channel=reservation.channel,
            customer=reservation.customer,
        )
        nuevo_total = precio.total
        avisos.extend(precio.warnings)
    except (PricingError, InvalidRentalPeriod) as exc:
        bloqueo = bloqueo or str(exc)

    return ChangePreview(
        pickup_at=nueva_recogida,
        return_at=nueva_devolucion,
        category=nueva_categoria,
        availability=disponibilidad,
        price=precio,
        current_total=reservation.total,
        new_total=nuevo_total,
        difference=nuevo_total - reservation.total,
        releases_vehicle=libera,
        vehicle_conflict=conflicto,
        blocked_reason=bloqueo,
        warnings=tuple(avisos),
    )


@transaction.atomic
def apply_change(
    *,
    reservation: Reservation,
    pickup_at=None,
    return_at=None,
    category=None,
    release_vehicle: bool = False,
    actor=None,
) -> Reservation:
    """Cambia fechas o categoria: revalida disponibilidad y recalcula precio.

    El coche asignado se suelta si el mostrador lo pide (porque estorba) o si
    deja de encajar con la categoria nueva. Todo en una transaccion: si la
    disponibilidad dice que no, no se ha movido nada.
    """
    _comprobar_factura(reservation, actor)

    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    nueva_categoria = category or reservation.category

    if reservation.vehicle_id and (
        release_vehicle or nueva_categoria.pk != reservation.vehicle.category_id
    ):
        soltado = reservation.vehicle
        reservation.vehicle = None
        reservation.needs_reassignment = True
        reservation.save(update_fields=["vehicle", "needs_reassignment", "updated_at"])
        logger.info(
            "vehiculo_soltado_por_cambio",
            reservation_number=reservation.number,
            plate=soltado.plate,
            actor_id=getattr(actor, "pk", None),
        )

    # Revalida con el grupo bloqueado y excluyendose a si misma.
    reservation = update_reservation_period(
        reservation=reservation,
        start=pickup_at,
        end=return_at,
        category=category,
        actor=actor,
    )

    # Se anotan antes de borrar las lineas: son las mismas que hay que volver a
    # congelar con el precio del alquiler nuevo.
    peticiones = _extras_como_peticion(reservation)

    breakdown = quote(
        category=reservation.category,
        pickup_office=reservation.pickup_office,
        return_office=reservation.return_office,
        pickup_at=reservation.pickup_at,
        return_at=reservation.return_at,
        extras=peticiones,
        channel=reservation.channel,
        customer=reservation.customer,
    )

    total_anterior = reservation.total
    for campo, valor in _campos_de_precio(breakdown).items():
        setattr(reservation, campo, valor)
    reservation.save()

    # Al cambiar los dias, un extra por dia cuesta otra cosa: se recongelan con
    # el calculo nuevo. Lo que nunca los mueve es un cambio en el maestro.
    reservation.extras.all().delete()
    _congelar_extras(reservation, breakdown, peticiones)

    logger.info(
        "reserva_modificada",
        reservation_number=reservation.number,
        pickup_at=reservation.pickup_at.isoformat(),
        return_at=reservation.return_at.isoformat(),
        category=reservation.category.code,
        total_anterior=str(total_anterior),
        total_nuevo=str(reservation.total),
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


@transaction.atomic
def release_vehicle(*, reservation: Reservation, actor=None) -> Reservation:
    """Suelta el coche asignado. La reserva sigue viva contra su categoria."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if not reservation.vehicle_id:
        return reservation

    soltado = reservation.vehicle
    reservation.vehicle = None
    reservation.needs_reassignment = True
    reservation.save(update_fields=["vehicle", "needs_reassignment", "updated_at"])
    logger.info(
        "vehiculo_liberado",
        reservation_number=reservation.number,
        plate=soltado.plate,
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


# ---------------------------------------------------------------------------
# Conductores adicionales
# ---------------------------------------------------------------------------


def validate_licence(*, driver, reservation: Reservation) -> None:
    """El carnet tiene que estar vigente durante todo el alquiler.

    No basta con que valga hoy: si caduca a mitad del alquiler, esa persona deja
    de poder conducir el coche antes de devolverlo.
    """
    if not driver.licence_number.strip():
        raise ReservationServiceError(_("El numero de carnet es obligatorio."))

    if driver.licence_expiry is None:
        raise ReservationServiceError(
            _("Falta la caducidad del carnet: sin ella no se puede autorizar.")
        )

    fin = timezone.localtime(reservation.return_at).date()
    if driver.licence_expiry < fin:
        raise ReservationServiceError(
            _(
                "El carnet caduca el %(caduca)s y el alquiler termina el %(fin)s: "
                "no se puede autorizar a %(nombre)s."
            )
            % {
                "caduca": driver.licence_expiry.strftime("%d/%m/%Y"),
                "fin": fin.strftime("%d/%m/%Y"),
                "nombre": f"{driver.first_name} {driver.last_name}".strip(),
            }
        )


@transaction.atomic
def add_driver(*, reservation: Reservation, driver, actor=None):
    """Autoriza a un conductor adicional."""
    driver.reservation = reservation
    validate_licence(driver=driver, reservation=reservation)
    driver.full_clean(exclude=["reservation"])
    driver.save()

    logger.info(
        "conductor_autorizado",
        reservation_number=reservation.number,
        driver=f"{driver.first_name} {driver.last_name}".strip(),
        licence=driver.licence_number,
        actor_id=getattr(actor, "pk", None),
    )
    return driver


@transaction.atomic
def remove_driver(*, driver, actor=None) -> None:
    """Retira la autorizacion.

    Se borra de verdad: es un permiso vivo, no un dato historico. Quien
    condujo cuando se entrego el coche queda en el contrato firmado.
    """
    datos = {
        "reservation_number": driver.reservation.number,
        "driver": f"{driver.first_name} {driver.last_name}".strip(),
        "actor_id": getattr(actor, "pk", None),
    }
    driver.delete()
    logger.info("conductor_retirado", **datos)
