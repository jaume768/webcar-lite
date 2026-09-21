"""Reglas de la entrega y la devolucion. Las vistas orquestan, aqui se decide."""

from decimal import Decimal

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.services import ServiceError
from apps.reservations.invoiced import ensure_not_invoiced
from apps.reservations.models import ChargeKind, ReservationCharge, ReservationStatus
from apps.reservations.services import record_pickup, record_return
from apps.reservations.state_machine import transition

from .charges import extra_km_charge, fuel_charge, late_return_charge, manual_charge
from .models import CheckIn, CheckOut, Damage

logger = structlog.get_logger(__name__)


class OperationsServiceError(ServiceError):
    """Regla de negocio incumplida en el mostrador."""


def preexisting_damages(reservation):
    """Danos que ya tenia el coche al entregarlo.

    Es lo que se precarga en la devolucion: sin esto, un aranazo de hace un mes
    se le cobraria al cliente de hoy.
    """
    return reservation.damages.filter(is_preexisting=True).prefetch_related("photos")


def new_damages(reservation):
    return reservation.damages.filter(is_preexisting=False).prefetch_related("photos")


def _guardar_danos(reservation, vehicle, partes, *, preexisting: bool, employee=None):
    creados = []
    for parte in partes:
        dano = Damage(
            reservation=reservation,
            vehicle=vehicle,
            zone=parte["zone"],
            damage_type=parte["damage_type"],
            severity=parte.get("severity", 1),
            description=parte.get("description", ""),
            is_preexisting=preexisting,
            estimated_cost=Decimal(parte.get("estimated_cost") or 0),
            # Un dano que ya estaba no se le cobra al cliente de hoy, diga lo
            # que diga el formulario.
            charge_to_customer=False if preexisting else bool(parte.get("charge_to_customer")),
            recorded_by=employee if getattr(employee, "pk", None) else None,
        )
        dano.full_clean(exclude=["reservation", "vehicle", "recorded_by"])
        dano.save()
        creados.append(dano)
    return creados


@transaction.atomic
def perform_check_in(
    *,
    reservation,
    mileage: int,
    fuel_level: int,
    licence_verified: bool,
    id_verified: bool,
    observations: str = "",
    actual_datetime=None,
    damages=(),
    employee=None,
) -> CheckIn:
    """Entrega el coche: deja el acta y pone la reserva en curso.

    Sin vehiculo asignado no hay entrega posible, y sin los documentos
    comprobados tampoco: son las dos cosas que no se pueden arreglar despues.
    """
    ensure_not_invoiced(reservation)
    if reservation.vehicle_id is None:
        raise OperationsServiceError(
            _("La reserva %(numero)s no tiene vehiculo asignado: asignalo antes de entregar.")
            % {"numero": reservation.number}
        )
    if hasattr(reservation, "check_in"):
        raise OperationsServiceError(
            _("La reserva %(numero)s ya tiene entrega registrada.") % {"numero": reservation.number}
        )
    if not (licence_verified and id_verified):
        raise OperationsServiceError(
            _("Hay que comprobar el carnet y el documento de identidad antes de entregar.")
        )

    momento = actual_datetime or timezone.now()
    entrega = CheckIn(
        reservation=reservation,
        vehicle=reservation.vehicle,
        actual_datetime=momento,
        mileage=mileage,
        fuel_level=fuel_level,
        observations=observations,
        licence_verified=licence_verified,
        id_verified=id_verified,
        employee=employee if getattr(employee, "pk", None) else None,
    )
    entrega.full_clean(exclude=["reservation", "vehicle", "employee"])
    entrega.save()

    # Lo anotado en la entrega es preexistente por definicion.
    _guardar_danos(reservation, reservation.vehicle, damages, preexisting=True, employee=employee)

    record_pickup(reservation=reservation, at=momento, actor=employee)
    transition(reservation, ReservationStatus.IN_PROGRESS, employee)

    # RD 933/2021: el contrato se comunica a SES.Hospedajes. Se prepara aqui
    # (datos del momento de la entrega) y, si esta completo, se encola.
    from apps.compliance.services import prepare_and_queue

    prepare_and_queue(reservation=reservation, actor=employee)

    audit.record(
        AuditAction.CHECK_IN,
        _("Entrega de %(numero)s: %(matricula)s con %(km)s km")
        % {"numero": reservation.number, "matricula": entrega.vehicle.plate, "km": mileage},
        obj=entrega,
        actor=employee,
        reservation=reservation,
        changes={"mileage": mileage, "fuel_level": fuel_level, "damages": len(damages)},
    )
    logger.info(
        "entrega_registrada",
        reservation_number=reservation.number,
        vehicle_id=entrega.vehicle_id,
        mileage=mileage,
        fuel_level=fuel_level,
        danos=len(damages),
        employee_id=getattr(employee, "pk", None),
    )
    return entrega


def compute_checkout_charges(
    *,
    reservation,
    check_in: CheckIn,
    mileage: int,
    fuel_level: int,
    actual_datetime,
    manual=(),
) -> list:
    """Cargos que genera esta devolucion. No escribe nada."""
    tax_rate = Decimal(settings.DEFAULT_TAX_RATE)
    desglose = reservation.price_breakdown or {}
    precio_dia = Decimal(desglose.get("daily_price") or "0.00")

    lineas = []
    km = extra_km_charge(
        included_km=reservation.included_km,
        mileage_out=check_in.mileage,
        mileage_in=mileage,
        price_per_km=Decimal(settings.EXTRA_KM_PRICE),
        tax_rate=tax_rate,
    )
    if km:
        lineas.append(km)

    combustible = fuel_charge(
        policy=reservation.fuel_policy,
        level_out=check_in.fuel_level,
        level_in=fuel_level,
        tank_liters=reservation.vehicle.tank_liters or settings.DEFAULT_TANK_LITERS,
        price_per_liter=Decimal(settings.FUEL_PRICE_PER_LITER),
        tax_rate=tax_rate,
    )
    if combustible:
        lineas.append(combustible)

    # La referencia es el periodo **previsto**, que es el que se facturo: asi
    # el cargo mide solo el retraso de la devolucion. Si se recoge antes o
    # despues de lo previsto, eso se arregla cambiando las fechas de la
    # reserva, no colandolo como dias de retraso.
    retraso = late_return_charge(
        pickup_at=reservation.pickup_at,
        planned_return_at=reservation.return_at,
        actual_return_at=actual_datetime,
        daily_price=precio_dia,
        tax_rate=tax_rate,
    )
    if retraso:
        lineas.append(retraso)

    for cargo in manual:
        if not cargo.get("amount"):
            continue
        lineas.append(
            manual_charge(
                kind=cargo.get("kind", ChargeKind.OTHER),
                concept=cargo["concept"],
                amount=Decimal(cargo["amount"]),
                tax_rate=tax_rate,
            )
        )

    return lineas


@transaction.atomic
def perform_check_out(
    *,
    reservation,
    mileage: int,
    fuel_level: int,
    return_office=None,
    observations: str = "",
    actual_datetime=None,
    damages=(),
    manual_charges=(),
    employee=None,
) -> CheckOut:
    """Devuelve el coche: calcula los cargos, cierra la reserva y mueve la flota."""
    ensure_not_invoiced(reservation)
    entrega = getattr(reservation, "check_in", None)
    if entrega is None:
        raise OperationsServiceError(
            _("La reserva %(numero)s no tiene entrega registrada: no se puede devolver.")
            % {"numero": reservation.number}
        )
    if hasattr(reservation, "check_out"):
        raise OperationsServiceError(
            _("La reserva %(numero)s ya esta devuelta.") % {"numero": reservation.number}
        )
    if mileage < entrega.mileage:
        raise OperationsServiceError(
            _("Los kilometros no pueden bajar: al entregar marcaba %(km)s.")
            % {"km": entrega.mileage}
        )

    momento = actual_datetime or timezone.now()
    oficina = return_office or reservation.return_office
    vehiculo = reservation.vehicle or entrega.vehicle

    devolucion = CheckOut(
        reservation=reservation,
        vehicle=vehiculo,
        return_office=oficina,
        actual_datetime=momento,
        mileage=mileage,
        fuel_level=fuel_level,
        observations=observations,
        employee=employee if getattr(employee, "pk", None) else None,
    )
    devolucion.full_clean(exclude=["reservation", "vehicle", "return_office", "employee"])
    devolucion.save()

    _guardar_danos(reservation, vehiculo, damages, preexisting=False, employee=employee)

    lineas = compute_checkout_charges(
        reservation=reservation,
        check_in=entrega,
        mileage=mileage,
        fuel_level=fuel_level,
        actual_datetime=momento,
        manual=manual_charges,
    )
    _guardar_cargos(reservation, lineas, employee=employee)

    record_return(reservation=reservation, at=momento, actor=employee)
    transition(reservation, ReservationStatus.FINISHED, employee)

    _devolver_a_flota(vehiculo, oficina, employee)

    audit.record(
        AuditAction.CHECK_OUT,
        _("Devolución de %(numero)s: %(km)s km, %(cargos)s cargos")
        % {"numero": reservation.number, "km": mileage, "cargos": len(lineas)},
        obj=devolucion,
        actor=employee,
        reservation=reservation,
        changes={"mileage": mileage, "fuel_level": fuel_level, "return_office": oficina.pk},
    )
    logger.info(
        "devolucion_registrada",
        reservation_number=reservation.number,
        vehicle_id=vehiculo.pk,
        mileage=mileage,
        km_recorridos=mileage - entrega.mileage,
        cargos=[str(linea.total) for linea in lineas],
        return_office_id=oficina.pk,
        employee_id=getattr(employee, "pk", None),
    )
    return devolucion


def _guardar_cargos(reservation, lineas, *, employee=None) -> Decimal:
    """Escribe los cargos como lineas de la reserva y actualiza el total."""
    for linea in lineas:
        ReservationCharge.objects.create(
            reservation=reservation,
            kind=linea.kind,
            concept=linea.concept,
            quantity=linea.quantity,
            unit_price=linea.unit_price,
            tax_rate=linea.tax_rate,
            base_amount=linea.base_amount,
            tax_amount=linea.tax_amount,
            total=linea.total,
            is_automatic=linea.kind
            in (ChargeKind.EXTRA_KM, ChargeKind.FUEL, ChargeKind.LATE_RETURN),
            created_by=employee if getattr(employee, "pk", None) else None,
        )
    return _refrescar_total_de_cargos(reservation)


def _refrescar_total_de_cargos(reservation) -> Decimal:
    from django.db.models import Sum

    total = reservation.charges.aggregate(suma=Sum("total"))["suma"] or Decimal("0.00")
    reservation.charges_total = total
    reservation.save(update_fields=["charges_total", "updated_at"])
    return total


def _devolver_a_flota(vehicle, office, employee=None) -> None:
    """Deja el coche donde se ha devuelto y en el estado configurado.

    La oficina se actualiza siempre: es la que manda para la disponibilidad,
    y un one-way que no la mueva deja la flota descuadrada.
    """
    from apps.fleet.models import VehicleStatus
    from apps.fleet.services import set_vehicle_status

    if vehicle.current_office_id != office.pk:
        vehicle.current_office = office
        vehicle.save(update_fields=["current_office"])
        logger.info("vehiculo_trasladado", vehicle_id=vehicle.pk, office_id=office.pk)

    destino = settings.VEHICLE_STATUS_AFTER_CHECKOUT
    if destino == VehicleStatus.AVAILABLE:
        # La transicion lo dejo en limpieza; la configuracion dice que aqui se
        # entrega listo para volver a salir.
        set_vehicle_status(vehicle=vehicle, status=VehicleStatus.AVAILABLE, actor=employee)


@transaction.atomic
def add_manual_charge(
    *, reservation, kind: str, concept: str, amount: Decimal, notes: str = "", employee=None
) -> ReservationCharge:
    """Cargo escrito a mano despues de la devolucion (limpieza, danos...)."""
    ensure_not_invoiced(reservation)
    if not concept.strip():
        raise OperationsServiceError(_("Un cargo sin concepto no se puede cobrar."))
    if Decimal(amount) <= 0:
        raise OperationsServiceError(_("El importe del cargo tiene que ser mayor que cero."))

    linea = manual_charge(
        kind=kind,
        concept=concept.strip(),
        amount=Decimal(amount),
        tax_rate=Decimal(settings.DEFAULT_TAX_RATE),
    )
    cargo = ReservationCharge.objects.create(
        reservation=reservation,
        kind=linea.kind,
        concept=linea.concept,
        quantity=linea.quantity,
        unit_price=linea.unit_price,
        tax_rate=linea.tax_rate,
        base_amount=linea.base_amount,
        tax_amount=linea.tax_amount,
        total=linea.total,
        is_automatic=False,
        notes=notes,
        created_by=employee if getattr(employee, "pk", None) else None,
    )
    _refrescar_total_de_cargos(reservation)
    logger.info(
        "cargo_manual_anadido",
        reservation_number=reservation.number,
        concepto=concept,
        importe=str(linea.total),
        employee_id=getattr(employee, "pk", None),
    )
    return cargo
