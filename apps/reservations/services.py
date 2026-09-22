"""Reglas de la reserva. Las vistas orquestan, aqui se decide.

El estado **no** se toca desde aqui: para eso esta `state_machine.transition()`.
Lo que vive en este modulo es el alta, el precio congelado y los apuntes que
dejan el check-in y el check-out.
"""

from dataclasses import dataclass
from decimal import Decimal

import structlog
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.availability.services import (
    check_category_availability,
    reserve_capacity,
    update_reservation_period,
    vehicle_conflicts,
)
from apps.core.services import ServiceError
from apps.pricing.dto import ExtraRequest, PriceBreakdown, PriceQuoteInput
from apps.pricing.models import Channel
from apps.pricing.services import (
    InvalidRentalPeriod,
    PricingError,
    calculate_reservation_price,
)

from .invoiced import InvoicedReservationError, ensure_not_invoiced
from .models import (
    CancellationPolicy,
    FuelPolicy,
    PriceChangeKind,
    Reservation,
    ReservationExtra,
    ReservationPriceChange,
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

    Las lineas se actualizan en su sitio en vez de borrarlas y recrearlas: un
    recalculo no puede cambiarles el id, porque son las filas a las que
    apuntaran manana las lineas de factura.
    """
    lineas = {linea.source_code: linea for linea in breakdown.lines_of("extra")}
    vivas = []
    for peticion in extras:
        linea = lineas.get(peticion.extra.code)
        if linea is None:
            # El motor no la cobro (tope, no aplicable...): no se guarda linea.
            continue
        fila, _creada = ReservationExtra.objects.update_or_create(
            reservation=reservation,
            extra=peticion.extra,
            defaults={
                "concept": linea.concept or peticion.extra.name,
                "quantity": peticion.quantity,
                "unit_price": linea.unit_price,
                "tax_rate": linea.tax_rate,
                "base_amount": linea.base,
                "tax_amount": linea.tax_amount,
                "total": linea.total,
            },
        )
        vivas.append(fila.pk)

    # Lo que ya no se vende, fuera.
    reservation.extras.exclude(pk__in=vivas).delete()


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
    origin: str = "",
) -> Reservation:
    """Alta rapida de mostrador: nace ya PENDIENTE y con precio cerrado.

    `origin` es el motivo que queda en el historico de estados; por defecto,
    el del mostrador. La API de reservas web pone el suyo.

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
        reason=origin or str(_("Alta rapida de mostrador")),
        changed_by=actor if getattr(actor, "pk", None) else None,
    )

    audit.record(
        AuditAction.CREATE,
        _("Reserva %(numero)s creada (%(total)s €)")
        % {"numero": reservation.number, "total": reservation.total},
        obj=reservation,
        actor=actor,
        changes={
            "pickup_at": reservation.pickup_at,
            "return_at": reservation.return_at,
            "category": reservation.category.code,
            "total": reservation.total,
        },
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
    _comprobar_factura(reservation)
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
    _comprobar_factura(reservation)
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


def _comprobar_factura(reservation: Reservation) -> None:
    """Lo facturado no se mueve: ni con permisos. Se corrige con rectificativa."""
    ensure_not_invoiced(reservation)


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
    #: Aviso de que el cambio pisaria un precio pactado a mano.
    manual_price_warning: str = ""
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
        _comprobar_factura(reservation)
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
        manual_price_warning=(
            _avisar_precio_manual(reservation) if reservation.is_price_manual else ""
        ),
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
    confirm_manual_override: bool = False,
    actor=None,
) -> Reservation:
    """Cambia fechas o categoria: revalida disponibilidad y recalcula precio.

    El coche asignado se suelta si el mostrador lo pide (porque estorba) o si
    deja de encajar con la categoria nueva. Todo en una transaccion: si la
    disponibilidad dice que no, no se ha movido nada.
    """
    _comprobar_factura(reservation)

    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    antes = {
        "pickup_at": reservation.pickup_at,
        "return_at": reservation.return_at,
        "category": reservation.category.code,
        "vehicle": reservation.vehicle.plate if reservation.vehicle_id else None,
    }
    # Un cambio de fechas o de categoria recalcula, y eso se llevaria por
    # delante un precio pactado a mano. Nunca en silencio.
    _comprobar_precio_manual(reservation, confirm_manual_override)
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
    # Al cambiar los dias, un extra por dia cuesta otra cosa: se recongelan con
    # el calculo nuevo. Lo que nunca los mueve es un cambio en el maestro.
    _aplicar_desglose(reservation, breakdown, peticiones)

    _registrar_cambio_de_precio(
        reservation,
        anterior=total_anterior,
        kind=PriceChangeKind.CATEGORY if category else PriceChangeKind.DATES,
        reason=str(_("Recalculado tras el cambio")),
        actor=actor,
    )

    audit.record(
        AuditAction.UPDATE,
        _("Reserva %(numero)s modificada") % {"numero": reservation.number},
        obj=reservation,
        actor=actor,
        changes=audit.diff(
            antes,
            {
                "pickup_at": reservation.pickup_at,
                "return_at": reservation.return_at,
                "category": reservation.category.code,
                "vehicle": reservation.vehicle.plate if reservation.vehicle_id else None,
            },
        ),
    )
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
    _comprobar_factura(reservation)
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if not reservation.vehicle_id:
        return reservation

    soltado = reservation.vehicle
    reservation.vehicle = None
    reservation.needs_reassignment = True
    reservation.save(update_fields=["vehicle", "needs_reassignment", "updated_at"])
    audit.record(
        AuditAction.VEHICLE,
        _("Coche soltado de %(numero)s") % {"numero": reservation.number},
        obj=reservation,
        actor=actor,
        changes={"vehicle": None},
    )
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
    _comprobar_factura(reservation)
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
    _comprobar_factura(driver.reservation)
    datos = {
        "reservation_number": driver.reservation.number,
        "driver": f"{driver.first_name} {driver.last_name}".strip(),
        "actor_id": getattr(actor, "pk", None),
    }
    driver.delete()
    logger.info("conductor_retirado", **datos)


# ---------------------------------------------------------------------------
# Precio: extras, precio manual y recalculo
# ---------------------------------------------------------------------------


class ManualPriceWouldBeLost(ReservationServiceError):
    """El recalculo pisaria un precio puesto a mano.

    Es el error clasico de estas pantallas: alguien acuerda un precio con el
    cliente, luego toca las fechas y el sistema recalcula por detras dejando la
    reserva a tarifa. Aqui no pasa en silencio: hay que confirmarlo.
    """


PERMISO_PRECIO_MANUAL = "reservations.change_reservation_price"


def _avisar_precio_manual(reservation: Reservation) -> str:
    return str(
        _(
            "%(numero)s tiene un precio puesto a mano (%(total)s EUR). Si sigues, "
            "se recalcula con la tarifa y ese acuerdo se pierde."
        )
        % {"numero": reservation.number, "total": reservation.total}
    )


def _comprobar_precio_manual(reservation: Reservation, confirmado: bool) -> None:
    if reservation.is_price_manual and not confirmado:
        raise ManualPriceWouldBeLost(_avisar_precio_manual(reservation))


def _registrar_cambio_de_precio(
    reservation: Reservation, *, anterior: Decimal, kind: str, reason: str = "", actor=None
) -> None:
    if anterior == reservation.total and kind != PriceChangeKind.MANUAL:
        return
    ReservationPriceChange.objects.create(
        reservation=reservation,
        kind=kind,
        previous_total=anterior,
        new_total=reservation.total,
        reason=reason,
        changed_by=actor if getattr(actor, "pk", None) else None,
    )
    audit.record(
        AuditAction.PRICE,
        _("Precio de %(numero)s: %(antes)s → %(despues)s €")
        % {"numero": reservation.number, "antes": anterior, "despues": reservation.total},
        obj=reservation,
        actor=actor,
        changes={"total": [anterior, reservation.total], "kind": kind, "reason": reason},
    )
    logger.info(
        "precio_de_reserva_cambiado",
        reservation_number=reservation.number,
        kind=kind,
        total_anterior=str(anterior),
        total_nuevo=str(reservation.total),
        motivo=reason,
        actor_id=getattr(actor, "pk", None),
    )


def _aplicar_desglose(
    reservation: Reservation,
    breakdown: PriceBreakdown,
    peticiones,
    *,
    manual: bool = False,
    reason: str = "",
) -> None:
    """Escribe el calculo en la reserva y vuelve a congelar los extras."""
    for campo, valor in _campos_de_precio(breakdown).items():
        setattr(reservation, campo, valor)
    reservation.is_price_manual = manual
    reservation.manual_price_reason = reason if manual else ""
    reservation.save()

    _congelar_extras(reservation, breakdown, peticiones)


def _recalcular(
    reservation: Reservation,
    *,
    peticiones=None,
    manual_override: Decimal | None = None,
) -> PriceBreakdown:
    if peticiones is None:
        peticiones = _extras_como_peticion(reservation)
    return quote(
        category=reservation.category,
        pickup_office=reservation.pickup_office,
        return_office=reservation.return_office,
        pickup_at=reservation.pickup_at,
        return_at=reservation.return_at,
        extras=peticiones,
        channel=reservation.channel,
        customer=reservation.customer,
        manual_override=manual_override,
    )


def price_preview(
    *,
    reservation: Reservation,
    extras=None,
    manual_override: Decimal | None = None,
) -> tuple[PriceBreakdown, Decimal]:
    """Calculo y diferencia contra el total actual, sin escribir nada."""
    breakdown = _recalcular(reservation, peticiones=extras, manual_override=manual_override)
    return breakdown, breakdown.total - reservation.total


@transaction.atomic
def add_extra(
    *,
    reservation: Reservation,
    extra,
    quantity: int = 1,
    confirm_manual_override: bool = False,
    actor=None,
) -> Reservation:
    """Anade un extra y recalcula el precio con el tope que tenga."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    _comprobar_factura(reservation)
    _comprobar_precio_manual(reservation, confirm_manual_override)

    if reservation.extras.filter(extra=extra).exists():
        raise ReservationServiceError(
            _("%(extra)s ya esta en la reserva. Cambia la cantidad en su linea.")
            % {"extra": extra.name}
        )
    if extra.max_quantity and quantity > extra.max_quantity:
        raise ReservationServiceError(
            _("De %(extra)s no se pueden poner mas de %(tope)s.")
            % {"extra": extra.name, "tope": extra.max_quantity}
        )

    anterior = reservation.total
    peticiones = (*_extras_como_peticion(reservation), ExtraRequest(extra=extra, quantity=quantity))
    breakdown = _recalcular(reservation, peticiones=peticiones)
    _aplicar_desglose(reservation, breakdown, peticiones)

    _registrar_cambio_de_precio(
        reservation,
        anterior=anterior,
        kind=PriceChangeKind.EXTRAS,
        reason=str(
            _("Anadido %(extra)s x%(cantidad)s") % {"extra": extra.name, "cantidad": quantity}
        ),
        actor=actor,
    )
    return reservation


@transaction.atomic
def set_extra_quantity(
    *,
    reservation: Reservation,
    line: ReservationExtra,
    quantity: int,
    confirm_manual_override: bool = False,
    actor=None,
) -> Reservation:
    """Cambia la cantidad de un extra ya vendido."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    _comprobar_factura(reservation)
    _comprobar_precio_manual(reservation, confirm_manual_override)

    if quantity < 1:
        raise ReservationServiceError(_("La cantidad tiene que ser al menos 1."))
    if line.extra.max_quantity and quantity > line.extra.max_quantity:
        raise ReservationServiceError(
            _("De %(extra)s no se pueden poner mas de %(tope)s.")
            % {"extra": line.extra.name, "tope": line.extra.max_quantity}
        )

    anterior = reservation.total
    peticiones = tuple(
        ExtraRequest(extra=otra.extra, quantity=quantity if otra.pk == line.pk else otra.quantity)
        for otra in reservation.extras.select_related("extra")
    )
    breakdown = _recalcular(reservation, peticiones=peticiones)
    _aplicar_desglose(reservation, breakdown, peticiones)

    _registrar_cambio_de_precio(
        reservation,
        anterior=anterior,
        kind=PriceChangeKind.EXTRAS,
        reason=str(
            _("%(extra)s pasa a x%(cantidad)s") % {"extra": line.extra.name, "cantidad": quantity}
        ),
        actor=actor,
    )
    return reservation


@transaction.atomic
def remove_extra(
    *,
    reservation: Reservation,
    line: ReservationExtra,
    confirm_manual_override: bool = False,
    actor=None,
) -> Reservation:
    """Quita un extra de la reserva y recalcula."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    _comprobar_factura(reservation)
    _comprobar_precio_manual(reservation, confirm_manual_override)

    nombre = line.concept
    anterior = reservation.total
    peticiones = tuple(
        ExtraRequest(extra=otra.extra, quantity=otra.quantity)
        for otra in reservation.extras.select_related("extra")
        if otra.pk != line.pk
    )
    breakdown = _recalcular(reservation, peticiones=peticiones)
    _aplicar_desglose(reservation, breakdown, peticiones)

    _registrar_cambio_de_precio(
        reservation,
        anterior=anterior,
        kind=PriceChangeKind.EXTRAS,
        reason=str(_("Quitado %(extra)s") % {"extra": nombre}),
        actor=actor,
    )
    return reservation


@transaction.atomic
def set_manual_price(
    *,
    reservation: Reservation,
    daily_price: Decimal,
    reason: str,
    actor=None,
) -> Reservation:
    """Fija a mano el precio por dia del alquiler.

    Exige permiso y motivo escrito. Los extras, suplementos e impuestos se
    siguen calculando: lo que se fuerza es el alquiler, que es lo que se negocia
    en el mostrador.
    """
    if actor is None or not actor.has_perm(PERMISO_PRECIO_MANUAL):
        raise PermissionDenied(
            _("Tu usuario no puede modificar el precio de una reserva (%(permiso)s).")
            % {"permiso": PERMISO_PRECIO_MANUAL}
        )
    motivo = (reason or "").strip()
    if not motivo:
        raise ReservationServiceError(
            _("Un precio puesto a mano no se guarda sin explicar por que.")
        )
    if daily_price is None or daily_price < 0:
        raise ReservationServiceError(_("El precio por dia no puede ser negativo."))

    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    _comprobar_factura(reservation)

    anterior = reservation.total
    peticiones = _extras_como_peticion(reservation)
    breakdown = _recalcular(reservation, peticiones=peticiones, manual_override=daily_price)
    _aplicar_desglose(reservation, breakdown, peticiones, manual=True, reason=motivo)

    _registrar_cambio_de_precio(
        reservation,
        anterior=anterior,
        kind=PriceChangeKind.MANUAL,
        reason=motivo,
        actor=actor,
    )
    return reservation


@transaction.atomic
def recalculate_price(
    *,
    reservation: Reservation,
    confirm_manual_override: bool = False,
    actor=None,
) -> Reservation:
    """Rehace el precio desde la tarifa, olvidando cualquier acuerdo manual."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    _comprobar_factura(reservation)
    _comprobar_precio_manual(reservation, confirm_manual_override)

    anterior = reservation.total
    peticiones = _extras_como_peticion(reservation)
    breakdown = _recalcular(reservation, peticiones=peticiones)
    _aplicar_desglose(reservation, breakdown, peticiones)

    _registrar_cambio_de_precio(
        reservation,
        anterior=anterior,
        kind=PriceChangeKind.RECALCULATED,
        reason=str(_("Recalculado desde tarifa")),
        actor=actor,
    )
    return reservation
