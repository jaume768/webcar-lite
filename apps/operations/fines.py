"""Multas: localizar la reserva, identificar al conductor y repercutirla.

La pieza clave es `find_reservation`: con la matricula y el momento de la
infraccion se sabe que reserva tenia el coche, y con ella quien lo conducia.
Se usan las horas reales de entrega y devolucion; si faltan, las previstas.
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.services import ServiceError
from apps.reservations.models import Reservation, ReservationStatus

from .models import FineStatus, TrafficFine

logger = structlog.get_logger(__name__)

#: Dias que da la ley para identificar al conductor (art. 11 LSV): 20 naturales.
DIAS_PARA_IDENTIFICAR = 20


class FineServiceError(ServiceError):
    """No se puede hacer eso con la multa."""


def find_reservation(*, vehicle, at):
    """La reserva que tenia el coche en ese momento, o None.

    Solo cuentan las que llegaron a salir (en curso o finalizadas): una
    reservada que no se entrego no pudo cometer la infraccion.
    """
    return (
        Reservation.objects.filter(
            vehicle=vehicle,
            status__in=(ReservationStatus.IN_PROGRESS, ReservationStatus.FINISHED),
        )
        .annotate(
            desde=Coalesce(F("actual_pickup_at"), F("pickup_at")),
            hasta=Coalesce(F("actual_return_at"), F("return_at")),
        )
        .filter(desde__lte=at, hasta__gte=at)
        .select_related("customer")
        .order_by("-desde")
        .first()
    )


def _direccion(cliente) -> str:
    partes = [cliente.address, f"{cliente.postal_code} {cliente.city}".strip(), cliente.country]
    return ", ".join(parte for parte in partes if parte)


def _asignar(multa: TrafficFine) -> None:
    """Pone reserva, cliente y datos del conductor. El titular del contrato responde."""
    reserva = find_reservation(vehicle=multa.vehicle, at=multa.offense_at)
    multa.reservation = reserva
    if reserva is None or reserva.customer is None:
        multa.customer = None
        multa.driver_name = multa.driver_document = multa.driver_licence = ""
        multa.driver_address = ""
        multa.status = FineStatus.NO_MATCH
        return
    cliente = reserva.customer
    multa.customer = cliente
    multa.driver_name = cliente.full_name
    multa.driver_document = cliente.document_number
    multa.driver_licence = cliente.licence_number
    multa.driver_address = _direccion(cliente)
    if multa.status in (FineStatus.RECEIVED, FineStatus.NO_MATCH, FineStatus.MATCHED):
        multa.status = FineStatus.MATCHED


@transaction.atomic
def register_fine(*, fine: TrafficFine, actor=None) -> TrafficFine:
    """Alta de una multa ya validada: busca la reserva y fija el plazo."""
    creando = fine.pk is None
    if fine.identify_by is None and fine.notified_on is not None:
        fine.identify_by = fine.notified_on + timedelta(days=DIAS_PARA_IDENTIFICAR)
    if fine.office_id is None:
        fine.office = fine.vehicle.current_office
    if creando:
        fine.created_by = actor if getattr(actor, "pk", None) else None
        _asignar(fine)
    fine.save()
    audit.record(
        AuditAction.FINE,
        (
            _("Multa %(expediente)s registrada: %(resultado)s")
            if creando
            else _("Multa %(expediente)s actualizada: %(resultado)s")
        )
        % {
            "expediente": fine.file_number,
            "resultado": fine.reservation.number if fine.reservation else _("sin reserva"),
        },
        obj=fine,
        actor=actor,
        reservation=fine.reservation,
        office_id=fine.office_id,
        changes={"plate": fine.vehicle.plate, "offense_at": fine.offense_at, "amount": fine.amount},
    )
    logger.info(
        "multa_registrada" if creando else "multa_actualizada",
        fine_id=fine.pk,
        reservation_id=fine.reservation_id,
        actor_id=getattr(actor, "pk", None),
    )
    return fine


@transaction.atomic
def rematch(*, fine: TrafficFine, actor=None) -> TrafficFine:
    """Vuelve a buscar la reserva: por si se corrigio la hora o la matricula."""
    multa = TrafficFine.objects.select_for_update().get(pk=fine.pk)
    if multa.status in (FineStatus.CHARGED, FineStatus.CLOSED):
        raise FineServiceError(_("La multa ya está facturada o cerrada."))
    _asignar(multa)
    multa.save()
    audit.record(
        AuditAction.FINE,
        _("Multa %(expediente)s reasignada") % {"expediente": multa.file_number},
        obj=multa,
        actor=actor,
        reservation=multa.reservation,
        office_id=multa.office_id,
    )
    return multa


@transaction.atomic
def mark_identified(*, fine: TrafficFine, actor=None, on=None) -> TrafficFine:
    """El conductor ya se ha comunicado al organismo."""
    multa = TrafficFine.objects.select_for_update().get(pk=fine.pk)
    if not multa.driver_name:
        raise FineServiceError(_("No hay conductor que identificar: falta la reserva."))
    if multa.status not in (FineStatus.MATCHED, FineStatus.RECEIVED):
        raise FineServiceError(_("La multa ya no está pendiente de identificar."))
    multa.status = FineStatus.IDENTIFIED
    multa.identified_on = on or timezone.localdate()
    multa.save(update_fields=["status", "identified_on", "updated_at"])
    audit.record(
        AuditAction.FINE,
        _("Conductor de la multa %(expediente)s identificado: %(conductor)s")
        % {"expediente": multa.file_number, "conductor": multa.driver_name},
        obj=multa,
        actor=actor,
        reservation=multa.reservation,
        office_id=multa.office_id,
    )
    return multa


@transaction.atomic
def attach_invoice(*, fine_id, invoice, actor=None) -> TrafficFine:
    """Enlaza la factura con la que se repercute la multa al cliente."""
    multa = TrafficFine.objects.for_user(actor).select_for_update().filter(pk=fine_id).first()
    if multa is None:
        raise FineServiceError(_("Esa multa no existe o no es de tus oficinas."))
    multa.invoice = invoice
    multa.status = FineStatus.CHARGED
    multa.save(update_fields=["invoice", "status", "updated_at"])
    audit.record(
        AuditAction.FINE,
        _("Multa %(expediente)s facturada en %(factura)s")
        % {"expediente": multa.file_number, "factura": invoice.number},
        obj=multa,
        actor=actor,
        reservation=multa.reservation,
        office_id=multa.office_id,
    )
    return multa


@transaction.atomic
def close_fine(*, fine: TrafficFine, actor=None) -> TrafficFine:
    multa = TrafficFine.objects.select_for_update().get(pk=fine.pk)
    multa.status = FineStatus.CLOSED
    multa.save(update_fields=["status", "updated_at"])
    audit.record(
        AuditAction.FINE,
        _("Multa %(expediente)s cerrada") % {"expediente": multa.file_number},
        obj=multa,
        actor=actor,
        reservation=multa.reservation,
        office_id=multa.office_id,
    )
    return multa


def admin_fee() -> Decimal:
    """Lo que se cobra por gestionar una multa, sin IVA."""
    return Decimal(str(settings.FINE_ADMIN_FEE))
