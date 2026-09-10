"""Motor de disponibilidad.

Responde a una sola pregunta: dada una categoria, una oficina y unas fechas,
?puedo aceptar otra reserva? Todo lo demas del sistema se apoya en esto.

Como se cuenta
--------------
La disponibilidad es un problema de **capacidad de la categoria dentro de un
grupo de oficinas**, no de una oficina suelta. Dentro de un grupo la flota se
mueve libremente, asi que un one-way de Palma al aeropuerto no cambia la
capacidad del grupo: el coche esta fuera durante el alquiler y vuelve al mismo
sitio del que salio.

    capacidad = vehiculos de la categoria en el grupo
    ocupacion = vehiculos bloqueados + reservas que ocupan flota
    libre     = capacidad - ocupacion

La ocupacion de una reserva incluye el rato de rotacion (limpieza y revision)
que va detras de la entrega, y por eso se compara contra `occupancy_period`,
que es una columna generada por Postgres y no algo que la aplicacion calcule
cada vez.

Las funciones de este modulo no tocan la request ni la sesion: reciben datos y
devuelven resultado.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.services import ServiceError
from apps.fleet.models import Vehicle, VehicleCategory, VehicleStatus
from apps.offices.models import Office
from apps.reservations.models import (
    CAPACITY_CONSUMING_STATUSES,
    Reservation,
    ReservationStatus,
)

from .models import CategoryLock

logger = structlog.get_logger(__name__)

#: Cuantas reservas en conflicto se nombran en un mensaje antes de resumir.
MAX_CONFLICTOS_EN_MENSAJE = 3


class AvailabilityError(ServiceError):
    """No hay hueco, o el que se pedia ya no esta libre."""


class NoAvailabilityError(AvailabilityError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class VehicleNotAvailableError(AvailabilityError):
    def __init__(self, message, conflicts=()):
        super().__init__(message)
        self.conflicts = tuple(conflicts)


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvailabilityResult:
    """Respuesta del motor, con el porque incluido.

    `occupied` junta bloqueos y reservas porque quien pregunta quiere saber
    cuantos coches no puede usar; el desglose queda en `blocked` y `reserved`
    para poder explicarlo en pantalla.
    """

    available: bool
    total_fleet: int
    occupied: int
    free: int
    blocking_reasons: list[str] = field(default_factory=list)

    # Desglose, para mensajes y pantallas.
    blocked: int = 0
    reserved: int = 0
    conflicting_codes: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.available


@dataclass(frozen=True)
class CategoryAvailability:
    category: VehicleCategory
    availability: AvailabilityResult


@dataclass(frozen=True)
class Override:
    """Autorizacion explicita para vender por encima de la capacidad."""

    user: object
    reason: str


# ---------------------------------------------------------------------------
# Ambito: grupo de oficinas
# ---------------------------------------------------------------------------


def pool_scope(office: Office) -> tuple[str, list[int]]:
    """Ambito sobre el que se cuenta la capacidad de `office`.

    Devuelve la clave del ambito (para el cerrojo) y los ids de las oficinas
    que comparten flota. Una oficina sin grupo responde solo de la suya.
    """
    if office.pool_id:
        ids = list(
            Office.objects.filter(pool_id=office.pool_id, is_active=True).values_list(
                "id", flat=True
            )
        )
        # La propia oficina cuenta aunque este desactivada: sus coches existen.
        if office.id not in ids:
            ids.append(office.id)
        return f"pool:{office.pool_id}", ids
    return f"office:{office.id}", [office.id]


def occupancy_range(start: datetime, end: datetime, rotation_minutes: int) -> DateTimeTZRange:
    """`[inicio, fin + rotacion)`.

    Se alarga por el final, nunca por el principio: la limpieza va detras de la
    entrega. Al alargar los dos lados de la comparacion (el periodo que se pide
    y el que ya esta guardado) queda garantizado un hueco de al menos la
    rotacion entre dos alquileres, sin contarlo dos veces.
    """
    return DateTimeTZRange(start, end + timedelta(minutes=rotation_minutes), "[)")


def _rotacion(rotation_minutes: int | None) -> int:
    return settings.VEHICLE_ROTATION_MINUTES if rotation_minutes is None else rotation_minutes


def _validar_periodo(start: datetime, end: datetime) -> None:
    if end <= start:
        raise AvailabilityError(_("La devolucion tiene que ser posterior a la recogida."))


def _pk(reserva_o_pk) -> int | None:
    if reserva_o_pk is None:
        return None
    return getattr(reserva_o_pk, "pk", reserva_o_pk)


# ---------------------------------------------------------------------------
# Consultas base
# ---------------------------------------------------------------------------


def fleet_for_scope(category: VehicleCategory, office_ids: list[int]):
    """Vehiculos que cuentan como capacidad del grupo.

    Un coche dado de baja o retirado no es flota. El estado del vehiculo
    (taller, limpieza) es de *ahora* y no dice nada de un periodo futuro: lo que
    quita capacidad en un periodo son los bloqueos, que si tienen fechas.
    """
    return Vehicle.objects.filter(
        category=category,
        current_office_id__in=office_ids,
        is_active=True,
    ).exclude(status=VehicleStatus.RETIRED)


def _reservas_que_ocupan(category, office_ids, period, exclude_pk=None):
    """Reservas que restan capacidad del grupo en ese periodo.

    Dos casos distintos, y la diferencia es el one-way:

    * Devolucion **dentro** del grupo: el coche vuelve, asi que ocupa solo
      durante su periodo.
    * Devolucion **fuera** del grupo: el coche no vuelve. Ocupa desde la
      recogida y sin fecha de fin, hasta que la reserva se cierre y el traslado
      quede reflejado en la oficina del vehiculo. Es deliberadamente
      conservador: preferimos no prometer un coche que no vamos a tener.
    """
    base = Reservation.objects.consuming_capacity().filter(
        category=category,
        pickup_office_id__in=office_ids,
    )
    if exclude_pk is not None:
        base = base.exclude(pk=exclude_pk)

    vuelve = Q(return_office_id__in=office_ids) & Q(occupancy_period__overlap=period)
    se_va = ~Q(return_office_id__in=office_ids) & Q(pickup_at__lt=period.upper)
    return base.filter(vuelve | se_va)


def _vehiculos_bloqueados(fleet_qs, period) -> set[int]:
    """Ids de vehiculos del grupo con un bloqueo que pisa el periodo."""
    return set(
        fleet_qs.filter(
            blocks__start_at__lt=period.upper,
            blocks__end_at__gt=period.lower,
        ).values_list("id", flat=True)
    )


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def check_category_availability(
    category: VehicleCategory,
    office: Office,
    start: datetime,
    end: datetime,
    exclude_reservation=None,
    *,
    rotation_minutes: int | None = None,
) -> AvailabilityResult:
    """?Queda algun coche de esta categoria en el grupo de `office`?

    `exclude_reservation` es obligatorio al revalidar una reserva que ya existe
    (cambio de fechas, de categoria o de oficina): sin el, la reserva se cuenta
    a si misma como ocupacion y se bloquea sola.
    """
    _validar_periodo(start, end)
    rotacion = _rotacion(rotation_minutes)
    period = occupancy_range(start, end, rotacion)
    _scope_key, office_ids = pool_scope(office)

    flota = fleet_for_scope(category, office_ids)
    total_fleet = flota.count()

    bloqueados = _vehiculos_bloqueados(flota, period)
    reservas = list(
        _reservas_que_ocupan(category, office_ids, period, _pk(exclude_reservation)).values_list(
            "number", "vehicle_id"
        )
    )

    # Un coche que ademas de bloqueado tuviera una reserva encima no puede
    # restar dos veces: ya lo hemos contado como bloqueado.
    reservas_que_restan = [
        (code, vehicle_id) for code, vehicle_id in reservas if vehicle_id not in bloqueados
    ]

    reserved = len(reservas_que_restan)
    blocked = len(bloqueados)
    occupied = blocked + reserved
    free = max(total_fleet - occupied, 0)

    razones = []
    if total_fleet == 0:
        razones.append(str(_("No hay vehiculos de esta categoria en este grupo de oficinas.")))
    else:
        if blocked:
            razones.append(
                str(_("%(n)s vehiculo(s) bloqueados por taller, limpieza o traslado."))
                % {"n": blocked}
            )
        if reserved:
            razones.append(
                str(_("%(n)s reserva(s) ya comprometidas en ese periodo.")) % {"n": reserved}
            )

    disponible = free > 0
    return AvailabilityResult(
        available=disponible,
        total_fleet=total_fleet,
        occupied=occupied,
        free=free,
        blocking_reasons=[] if disponible else razones,
        blocked=blocked,
        reserved=reserved,
        conflicting_codes=tuple(code for code, _v in reservas_que_restan),
    )


def get_available_categories(
    office: Office,
    start: datetime,
    end: datetime,
    *,
    include_unavailable: bool = False,
    rotation_minutes: int | None = None,
) -> list[CategoryAvailability]:
    """Que se puede ofrecer en ese mostrador para esas fechas.

    Solo categorias activas: una categoria retirada sigue leyendose en el
    historico, pero no se vuelve a vender.
    """
    _validar_periodo(start, end)
    resultados = []
    for category in VehicleCategory.objects.active().order_by("sort_order", "name"):
        resultado = check_category_availability(
            category, office, start, end, rotation_minutes=rotation_minutes
        )
        if resultado.available or include_unavailable:
            resultados.append(CategoryAvailability(category=category, availability=resultado))
    return resultados


def vehicle_conflicts(
    vehicle: Vehicle,
    start: datetime,
    end: datetime,
    exclude_reservation=None,
    *,
    rotation_minutes: int | None = None,
):
    """Que impide usar ese coche en ese periodo: reservas y bloqueos."""
    period = occupancy_range(start, end, _rotacion(rotation_minutes))

    reservas = Reservation.objects.consuming_capacity().filter(
        vehicle=vehicle, occupancy_period__overlap=period
    )
    exclude_pk = _pk(exclude_reservation)
    if exclude_pk is not None:
        reservas = reservas.exclude(pk=exclude_pk)

    bloqueos = vehicle.blocks.filter(start_at__lt=period.upper, end_at__gt=period.lower)
    return list(reservas), list(bloqueos)


def check_vehicle_availability(
    vehicle: Vehicle,
    start: datetime,
    end: datetime,
    exclude_reservation=None,
    *,
    rotation_minutes: int | None = None,
) -> bool:
    """?Puede este coche concreto cubrir ese periodo?"""
    _validar_periodo(start, end)
    if not vehicle.is_active or vehicle.status == VehicleStatus.RETIRED:
        return False

    reservas, bloqueos = vehicle_conflicts(
        vehicle, start, end, exclude_reservation, rotation_minutes=rotation_minutes
    )
    return not reservas and not bloqueos


def get_available_vehicles(
    category: VehicleCategory,
    office: Office,
    start: datetime,
    end: datetime,
    exclude_reservation=None,
    *,
    rotation_minutes: int | None = None,
):
    """Coches concretos de la categoria libres en ese periodo dentro del grupo."""
    _validar_periodo(start, end)
    period = occupancy_range(start, end, _rotacion(rotation_minutes))
    _scope_key, office_ids = pool_scope(office)

    ocupados = Reservation.objects.consuming_capacity().filter(
        vehicle__isnull=False, occupancy_period__overlap=period
    )
    exclude_pk = _pk(exclude_reservation)
    if exclude_pk is not None:
        ocupados = ocupados.exclude(pk=exclude_pk)

    return (
        fleet_for_scope(category, office_ids)
        .exclude(id__in=ocupados.values("vehicle_id"))
        .exclude(blocks__start_at__lt=period.upper, blocks__end_at__gt=period.lower)
        .order_by("plate")
    )


# ---------------------------------------------------------------------------
# Escritura con control de concurrencia
# ---------------------------------------------------------------------------


def _acquire_scope_lock(category: VehicleCategory, office: Office) -> tuple[str, list[int]]:
    """Se pone en fila para contar la capacidad de esa categoria en ese grupo.

    A partir de aqui, cualquier otra transaccion que quiera reservar de la
    misma categoria y grupo espera. Sin esto, dos empleados cuentan a la vez,
    los dos ven el ultimo coche libre y los dos lo venden.
    """
    scope_key, office_ids = pool_scope(office)
    # get_or_create se protege sola con un savepoint: si dos transacciones
    # crean el cerrojo a la vez, la que pierde recupera la fila existente.
    lock, _creado = CategoryLock.objects.get_or_create(category=category, scope_key=scope_key)
    CategoryLock.objects.select_for_update().get(pk=lock.pk)
    return scope_key, office_ids


def _validar_override(override: Override | None) -> None:
    if override is None:
        return
    if not override.reason or not override.reason.strip():
        raise AvailabilityError(
            _("Para forzar una reserva sin disponibilidad hay que escribir el motivo.")
        )
    if not override.user or not override.user.has_perm("availability.override_availability"):
        raise AvailabilityError(
            _("Tu usuario no puede forzar una reserva por encima de la capacidad.")
        )


def _mensaje_sin_hueco(category, resultado: AvailabilityResult) -> str:
    partes = list(resultado.blocking_reasons)
    if resultado.conflicting_codes:
        codigos = ", ".join(resultado.conflicting_codes[:MAX_CONFLICTOS_EN_MENSAJE])
        if len(resultado.conflicting_codes) > MAX_CONFLICTOS_EN_MENSAJE:
            codigos += "..."
        partes.append(str(_("En conflicto: %(codigos)s.")) % {"codigos": codigos})

    return str(
        _(
            "No queda disponibilidad de %(categoria)s en ese periodo "
            "(%(libres)s libres de %(total)s). %(detalle)s"
        )
        % {
            "categoria": category.name,
            "libres": resultado.free,
            "total": resultado.total_fleet,
            "detalle": " ".join(partes),
        }
    ).strip()


def _mensaje_vehiculo_ocupado(vehicle, reservas, bloqueos) -> str:
    partes = []
    if reservas:
        codigos = ", ".join(r.number for r in reservas[:MAX_CONFLICTOS_EN_MENSAJE])
        if len(reservas) > MAX_CONFLICTOS_EN_MENSAJE:
            codigos += "..."
        partes.append(str(_("ya lo tiene la reserva %(codigos)s")) % {"codigos": codigos})
    if bloqueos:
        motivos = ", ".join(sorted({b.get_reason_display() for b in bloqueos}))
        partes.append(str(_("esta bloqueado por %(motivos)s")) % {"motivos": motivos})

    return str(
        _("%(matricula)s no esta libre en ese periodo: %(detalle)s.")
        % {"matricula": vehicle.plate, "detalle": " y ".join(partes)}
    )


def _comprobar_vehiculo(vehicle, start, end, exclude_reservation, rotation_minutes):
    if not vehicle.is_active or vehicle.status == VehicleStatus.RETIRED:
        raise VehicleNotAvailableError(
            str(_("%(matricula)s ya no esta en flota.")) % {"matricula": vehicle.plate}
        )

    reservas, bloqueos = vehicle_conflicts(
        vehicle, start, end, exclude_reservation, rotation_minutes=rotation_minutes
    )
    if reservas or bloqueos:
        raise VehicleNotAvailableError(
            _mensaje_vehiculo_ocupado(vehicle, reservas, bloqueos),
            conflicts=reservas,
        )


def _registrar_override(reservation: Reservation, override: Override, resultado) -> None:
    reservation.overbooked = True
    reservation.override_reason = override.reason.strip()
    reservation.override_by = override.user
    # La auditoria completa llega en su prompt; el motivo queda ya en la propia
    # reserva, que es donde nadie lo puede perder de vista.
    logger.warning(
        "overbooking_autorizado",
        reservation_number=reservation.number,
        category_id=reservation.category_id,
        pickup_office_id=reservation.pickup_office_id,
        libre=getattr(resultado, "free", None),
        total_flota=getattr(resultado, "total_fleet", None),
        motivo=reservation.override_reason,
        autorizado_por=override.user.pk,
    )


@transaction.atomic
def reserve_capacity(
    *,
    category: VehicleCategory,
    pickup_office: Office,
    return_office: Office | None = None,
    start: datetime,
    end: datetime,
    vehicle: Vehicle | None = None,
    status: str = ReservationStatus.PENDING,
    override: Override | None = None,
    actor=None,
    rotation_minutes: int | None = None,
    **extra_fields,
) -> Reservation:
    """Crea una reserva recontando la capacidad con el grupo bloqueado.

    Este es el unico camino para crear una reserva que ocupe flota. El recuento
    y la insercion van dentro de la misma transaccion y detras del mismo
    cerrojo, que es lo que hace que dos peticiones simultaneas no puedan vender
    el mismo ultimo coche.
    """
    _validar_periodo(start, end)
    _validar_override(override)
    return_office = return_office or pickup_office
    rotacion = _rotacion(rotation_minutes)

    resultado = None
    # Un borrador no compromete nada, asi que no hace falta contar ni bloquear.
    if status in CAPACITY_CONSUMING_STATUSES:
        _acquire_scope_lock(category, pickup_office)

        # Si ya hay coche elegido, se comprueba antes: "ese coche lo tiene la
        # reserva X" es accionable, y "no queda categoria" no lo es tanto.
        if vehicle is not None:
            _comprobar_vehiculo(vehicle, start, end, None, rotacion)

        resultado = check_category_availability(
            category, pickup_office, start, end, rotation_minutes=rotacion
        )
        if not resultado.available and override is None:
            raise NoAvailabilityError(_mensaje_sin_hueco(category, resultado), result=resultado)

    reservation = Reservation(
        category=category,
        vehicle=vehicle,
        pickup_office=pickup_office,
        return_office=return_office,
        pickup_at=start,
        return_at=end,
        status=status,
        rotation_minutes=rotacion,
        **extra_fields,
    )
    if override is not None:
        _registrar_override(reservation, override, resultado)

    reservation.save()

    logger.info(
        "reserva_creada",
        reservation_number=reservation.number,
        category_id=category.pk,
        pickup_office_id=pickup_office.pk,
        return_office_id=return_office.pk,
        vehicle_id=getattr(vehicle, "pk", None),
        status=status,
        libre_tras_reservar=(resultado.free - 1) if resultado else None,
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


@transaction.atomic
def update_reservation_period(
    *,
    reservation: Reservation,
    start: datetime | None = None,
    end: datetime | None = None,
    category: VehicleCategory | None = None,
    pickup_office: Office | None = None,
    return_office: Office | None = None,
    override: Override | None = None,
    actor=None,
) -> Reservation:
    """Cambia fechas, categoria u oficinas revalidando la disponibilidad.

    Se excluye siempre a si misma del recuento: una reserva que alarga sus
    fechas no puede aparecer como su propio obstaculo.
    """
    _validar_override(override)

    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
    nuevo_inicio = start or reservation.pickup_at
    nuevo_fin = end or reservation.return_at
    nueva_categoria = category or reservation.category
    nueva_recogida = pickup_office or reservation.pickup_office
    nueva_devolucion = return_office or reservation.return_office

    _validar_periodo(nuevo_inicio, nuevo_fin)

    if reservation.status in CAPACITY_CONSUMING_STATUSES:
        _acquire_scope_lock(nueva_categoria, nueva_recogida)

        # El coche asignado primero: al alargar una reserva en curso, lo que
        # hay que decirle al mostrador es que reserva tiene ese coche detras.
        if reservation.vehicle_id:
            _comprobar_vehiculo(
                reservation.vehicle,
                nuevo_inicio,
                nuevo_fin,
                reservation,
                reservation.rotation_minutes,
            )

        resultado = check_category_availability(
            nueva_categoria,
            nueva_recogida,
            nuevo_inicio,
            nuevo_fin,
            exclude_reservation=reservation,
            rotation_minutes=reservation.rotation_minutes,
        )
        if not resultado.available and override is None:
            raise NoAvailabilityError(
                _mensaje_sin_hueco(nueva_categoria, resultado), result=resultado
            )

        if override is not None:
            _registrar_override(reservation, override, resultado)

    reservation.pickup_at = nuevo_inicio
    reservation.return_at = nuevo_fin
    reservation.category = nueva_categoria
    reservation.pickup_office = nueva_recogida
    reservation.return_office = nueva_devolucion
    reservation.save()

    logger.info(
        "reserva_reprogramada",
        reservation_number=reservation.number,
        pickup_at=nuevo_inicio.isoformat(),
        return_at=nuevo_fin.isoformat(),
        actor_id=getattr(actor, "pk", None),
    )
    return reservation


@transaction.atomic
def assign_vehicle(
    *,
    reservation: Reservation,
    vehicle: Vehicle,
    actor=None,
) -> Reservation:
    """Pone un coche concreto a una reserva que iba contra la categoria."""
    reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)

    if vehicle.category_id != reservation.category_id:
        raise VehicleNotAvailableError(
            str(_("%(matricula)s no es de la categoria %(categoria)s."))
            % {"matricula": vehicle.plate, "categoria": reservation.category.name}
        )

    _comprobar_vehiculo(
        vehicle,
        reservation.pickup_at,
        reservation.return_at,
        reservation,
        reservation.rotation_minutes,
    )

    reservation.vehicle = vehicle
    reservation.needs_reassignment = False
    reservation.save(update_fields=["vehicle", "needs_reassignment", "updated_at"])

    logger.info(
        "vehiculo_asignado_a_reserva",
        reservation_number=reservation.number,
        vehicle_id=vehicle.pk,
        plate=vehicle.plate,
        actor_id=getattr(actor, "pk", None),
    )
    return reservation
