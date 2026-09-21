"""Mantenimiento: del aviso de que toca a la salida del taller.

Tres momentos con consecuencias:

- **Entrar en taller**: si inmoviliza, se crea un bloqueo en el calendario (la
  disponibilidad lo resta) y las reservas que tenian ese coche en esas fechas
  lo sueltan y quedan marcadas para darles otro. No se cancela ninguna.
- **Salir**: se quita el bloqueo, el coche vuelve a estar disponible si no hay
  otro motivo para que no lo este, y se apuntan kilometros y, si era una ITV,
  la nueva caducidad.
- **Proximos vencimientos**: lo hecho con `next_due_*` avisa antes de que toque.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.services import ServiceError

from .models import (
    BlockReason,
    MaintenanceKind,
    MaintenanceRecord,
    MaintenanceStatus,
    Vehicle,
    VehicleBlock,
    VehicleStatus,
)

logger = structlog.get_logger(__name__)

#: Con cuanta antelacion avisa un vencimiento por fecha o por kilometros.
DIAS_DE_AVISO = 30
KM_DE_AVISO = 1000


class MaintenanceError(ServiceError):
    """No se puede hacer eso con el mantenimiento."""


def _auditar(registro, mensaje, actor, **cambios):
    audit.record(
        AuditAction.MAINTENANCE,
        mensaje,
        obj=registro,
        actor=actor,
        office_id=registro.vehicle.current_office_id,
        changes=cambios,
    )


@transaction.atomic
def save_record(*, record: MaintenanceRecord, actor=None) -> MaintenanceRecord:
    """Alta o edicion de un mantenimiento ya validado por su formulario."""
    if record.status in (MaintenanceStatus.DONE, MaintenanceStatus.CANCELLED) and record.pk:
        anterior = MaintenanceRecord.objects.get(pk=record.pk)
        if anterior.status != record.status:
            raise MaintenanceError(_("El estado se cambia con las acciones, no editando."))
    creando = record.pk is None
    record.save()
    _auditar(
        record,
        (_("Mantenimiento programado: %(que)s") if creando else _("Mantenimiento editado: %(que)s"))
        % {"que": record},
        actor,
        scheduled_for=record.scheduled_for,
    )
    return record


def _soltar_reservas(vehiculo, desde, hasta, actor) -> list:
    """Suelta el coche de las reservas que caen en el periodo de taller."""
    from apps.reservations.models import Reservation, ReservationStatus

    afectadas = list(
        Reservation.objects.select_for_update().filter(
            vehicle=vehiculo,
            status__in=(ReservationStatus.PENDING, ReservationStatus.CONFIRMED),
            pickup_at__lt=hasta,
            return_at__gt=desde,
        )
    )
    if afectadas:
        Reservation.objects.filter(pk__in=[r.pk for r in afectadas]).update(
            vehicle=None, needs_reassignment=True, updated_at=timezone.now()
        )
        for reserva in afectadas:
            audit.record(
                AuditAction.VEHICLE,
                _("%(matricula)s entra en taller: %(numero)s queda sin coche")
                % {"matricula": vehiculo.plate, "numero": reserva.number},
                obj=reserva,
                actor=actor,
            )
    return afectadas


@transaction.atomic
def start_workshop(
    *, record: MaintenanceRecord, started_at=None, expected_end_at=None, actor=None
) -> list:
    """Mete el coche en taller. Devuelve las reservas que se han quedado sin coche."""
    from .services import FleetServiceError, save_block

    registro = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
    if registro.status != MaintenanceStatus.SCHEDULED:
        raise MaintenanceError(_("Solo entra en taller lo que está programado."))
    inicio = started_at or timezone.now()
    fin = expected_end_at or registro.expected_end_at or inicio + timedelta(days=1)
    if fin <= inicio:
        raise MaintenanceError(_("La salida prevista tiene que ser posterior a la entrada."))

    vehiculo = Vehicle.objects.select_for_update().get(pk=registro.vehicle_id)
    if vehiculo.status == VehicleStatus.RENTED:
        raise MaintenanceError(
            _("%(matricula)s está alquilado ahora mismo: recíbelo antes de llevarlo al taller.")
            % {"matricula": vehiculo.plate}
        )

    afectadas = []
    if registro.immobilizes:
        try:
            bloqueo = save_block(
                block=VehicleBlock(
                    vehicle=vehiculo,
                    start_at=inicio,
                    end_at=fin,
                    reason=BlockReason.WORKSHOP,
                    notes=str(registro),
                ),
                actor=actor,
            )
        except FleetServiceError as exc:
            raise MaintenanceError(str(exc)) from exc
        registro.block = bloqueo
        afectadas = _soltar_reservas(vehiculo, inicio, fin, actor)
        if inicio <= timezone.now():
            vehiculo.status = VehicleStatus.WORKSHOP
            vehiculo.save(update_fields=["status"])

    registro.status = MaintenanceStatus.IN_WORKSHOP
    registro.started_at = inicio
    registro.expected_end_at = fin
    registro.save()
    _auditar(
        registro,
        _("%(que)s: entra en taller hasta el %(fin)s")
        % {"que": registro, "fin": timezone.localtime(fin).strftime("%d/%m %H:%M")},
        actor,
        reservas_sin_coche=[r.number for r in afectadas],
    )
    logger.info(
        "coche_en_taller",
        record_id=registro.pk,
        vehicle_id=vehiculo.pk,
        reservas_sin_coche=[r.number for r in afectadas],
        actor_id=getattr(actor, "pk", None),
    )
    return afectadas


@transaction.atomic
def finish_workshop(
    *,
    record: MaintenanceRecord,
    finished_at=None,
    mileage: int | None = None,
    cost=None,
    supplier_invoice: str = "",
    next_due_date: date | None = None,
    next_due_km: int | None = None,
    actor=None,
) -> MaintenanceRecord:
    """Da por hecho el mantenimiento y devuelve el coche a la flota."""
    from .services import delete_block

    registro = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
    if registro.status not in (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_WORKSHOP):
        raise MaintenanceError(_("Ese mantenimiento ya está cerrado."))

    vehiculo = Vehicle.objects.select_for_update().get(pk=registro.vehicle_id)
    if registro.block_id:
        bloqueo = registro.block
        registro.block = None
        registro.save(update_fields=["block"])
        delete_block(block=bloqueo, actor=actor)

    campos = []
    if mileage is not None and mileage > vehiculo.mileage:
        vehiculo.mileage = mileage
        campos.append("mileage")
    if registro.kind == MaintenanceKind.ITV and next_due_date:
        vehiculo.itv_expiry = next_due_date
        campos.append("itv_expiry")
    if vehiculo.status == VehicleStatus.WORKSHOP and not vehiculo.is_blocked_at():
        vehiculo.status = VehicleStatus.AVAILABLE
        campos.append("status")
    if campos:
        vehiculo.save(update_fields=campos)

    registro.status = MaintenanceStatus.DONE
    registro.finished_at = finished_at or timezone.now()
    registro.mileage = mileage if mileage is not None else registro.mileage
    registro.cost = cost if cost is not None else registro.cost
    registro.supplier_invoice = supplier_invoice or registro.supplier_invoice
    registro.next_due_date = next_due_date
    registro.next_due_km = next_due_km
    registro.save()
    _auditar(
        registro,
        _("%(que)s: hecho") % {"que": registro},
        actor,
        mileage=mileage,
        cost=cost,
        next_due_date=next_due_date,
        next_due_km=next_due_km,
    )
    return registro


@transaction.atomic
def cancel_record(*, record: MaintenanceRecord, actor=None) -> MaintenanceRecord:
    from .services import delete_block

    registro = MaintenanceRecord.objects.select_for_update().get(pk=record.pk)
    if registro.status == MaintenanceStatus.DONE:
        raise MaintenanceError(_("Lo hecho no se anula."))
    if registro.block_id:
        bloqueo = registro.block
        registro.block = None
        registro.save(update_fields=["block"])
        delete_block(block=bloqueo, actor=actor)
        vehiculo = registro.vehicle
        if vehiculo.status == VehicleStatus.WORKSHOP and not vehiculo.is_blocked_at():
            vehiculo.status = VehicleStatus.AVAILABLE
            vehiculo.save(update_fields=["status"])
    registro.status = MaintenanceStatus.CANCELLED
    registro.save(update_fields=["status", "updated_at"])
    _auditar(registro, _("%(que)s: anulado") % {"que": registro}, actor)
    return registro


# ---------------------------------------------------------------------------
# Proximos vencimientos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DueItem:
    vehicle: Vehicle
    label: str
    due_date: date | None = None
    due_km: int | None = None
    overdue: bool = False

    @property
    def km_left(self) -> int | None:
        if self.due_km is None:
            return None
        return self.due_km - self.vehicle.mileage


def upcoming(user, *, today: date | None = None) -> list[DueItem]:
    """Lo que toca pronto o ya se ha pasado, del mas urgente al menos.

    Salen: el ultimo vencimiento de cada tipo en cada coche (por fecha o por
    kilometros), la ITV y el seguro de la ficha del coche, y lo programado.
    """
    hoy = today or timezone.localdate()
    limite = hoy + timedelta(days=DIAS_DE_AVISO)
    coches = {
        coche.pk: coche
        for coche in Vehicle.objects.active().for_user(user).exclude(status=VehicleStatus.RETIRED)
    }
    avisos: list[DueItem] = []

    ultimos = (
        MaintenanceRecord.objects.filter(vehicle_id__in=coches, status=MaintenanceStatus.DONE)
        .exclude(next_due_date__isnull=True, next_due_km__isnull=True)
        .order_by("vehicle_id", "kind", "-finished_at")
        .distinct("vehicle_id", "kind")
    )
    for registro in ultimos:
        coche = coches[registro.vehicle_id]
        por_fecha = registro.next_due_date is not None and registro.next_due_date <= limite
        por_km = (
            registro.next_due_km is not None and coche.mileage >= registro.next_due_km - KM_DE_AVISO
        )
        if por_fecha or por_km:
            avisos.append(
                DueItem(
                    vehicle=coche,
                    label=registro.get_kind_display(),
                    due_date=registro.next_due_date,
                    due_km=registro.next_due_km,
                    overdue=(registro.next_due_date is not None and registro.next_due_date < hoy)
                    or (registro.next_due_km is not None and coche.mileage >= registro.next_due_km),
                )
            )

    for coche in coches.values():
        de_la_ficha = ((_("ITV"), coche.itv_expiry), (_("Seguro"), coche.insurance_expiry))
        for etiqueta, fecha in de_la_ficha:
            if fecha is not None and fecha <= limite:
                avisos.append(
                    DueItem(vehicle=coche, label=str(etiqueta), due_date=fecha, overdue=fecha < hoy)
                )

    return sorted(
        avisos,
        key=lambda aviso: (not aviso.overdue, aviso.due_date or date.max, aviso.vehicle.plate),
    )
