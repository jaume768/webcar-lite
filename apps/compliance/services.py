"""Preparar y enviar los partes a SES.Hospedajes.

El envio real solo ocurre con `SES_ENABLED=True` y las credenciales puestas.
Sin eso, el parte se valida y se guarda con su XML (listo para subirlo a mano
en la sede) y queda marcado como simulado: nunca se da por enviado algo que no
ha salido de aqui.
"""

import base64
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.http import HttpError, post_json
from apps.core.services import ServiceError

from . import ses
from .models import SesStatus, SesSubmission

logger = structlog.get_logger(__name__)


class SesError(ServiceError):
    """No se puede preparar o enviar el parte."""


def _medio_de_pago(reserva) -> str:
    cobro = reserva.payments.filter(amount__gt=0).order_by("paid_at").first()
    return cobro.method if cobro else ""


@transaction.atomic
def prepare(*, reservation, actor=None) -> SesSubmission:
    """Arma (o rehace) el parte con los datos de ahora y dice que falta."""
    from apps.reservations.models import Reservation

    reserva = (
        Reservation.objects.select_related("customer", "vehicle", "pickup_office", "return_office")
        .select_for_update(of=("self",))
        .get(pk=reservation.pk)
    )
    parte, _creado = SesSubmission.objects.select_for_update().get_or_create(reservation=reserva)
    if parte.status in (SesStatus.SENT, SesStatus.ACCEPTED):
        return parte

    datos = ses.build_payload(
        reserva,
        landlord_code=settings.SES_LANDLORD_CODE,
        drivers=list(reserva.drivers.all()),
        payment_method=_medio_de_pago(reserva),
    )
    faltan = ses.missing_fields(datos)
    parte.payload = datos
    parte.missing = faltan
    parte.xml = ses.to_xml(datos)
    parte.status = SesStatus.INCOMPLETE if faltan else SesStatus.READY
    if parte.deadline_at is None:
        inicio = reserva.actual_pickup_at or reserva.pickup_at
        parte.deadline_at = inicio + timedelta(hours=settings.SES_DEADLINE_HOURS)
    parte.save()
    audit.record(
        AuditAction.COMPLIANCE,
        (
            _("Parte SES de %(numero)s preparado")
            if not faltan
            else _("Parte SES de %(numero)s: faltan %(n)s datos")
        )
        % {"numero": reserva.number, "n": len(faltan)},
        obj=parte,
        actor=actor,
        reservation=reserva,
        changes={"missing": faltan},
    )
    return parte


def _enviar(parte: SesSubmission) -> tuple[bool, str, str]:
    """POST al servicio. Devuelve (aceptado, codigo de lote, texto de respuesta)."""
    credenciales = base64.b64encode(
        f"{settings.SES_USER}:{settings.SES_PASSWORD}".encode()
    ).decode()
    respuesta = post_json(
        settings.SES_ENDPOINT,
        {
            "codigoArrendador": settings.SES_LANDLORD_CODE,
            "aplicacion": settings.SES_APPLICATION,
            "tipoOperacion": "A",
            "tipoComunicacion": ses.TIPO_COMUNICACION,
            "fichero": base64.b64encode(parte.xml.encode("utf-8")).decode(),
        },
        headers={"Authorization": f"Basic {credenciales}"},
    )
    if not respuesta.ok:
        return False, "", f"HTTP {respuesta.status}: {respuesta.body[:2000]}"
    try:
        cuerpo = respuesta.json() or {}
    except ValueError:
        cuerpo = {}
    lote = str(cuerpo.get("lote") or cuerpo.get("codigoLote") or "")
    aceptado = str(cuerpo.get("codigo", "0")) in ("0", "") and bool(lote or cuerpo)
    return aceptado, lote, respuesta.body[:4000]


@transaction.atomic
def send(*, submission: SesSubmission, actor=None) -> SesSubmission:
    """Envia el parte si esta completo. Sin envio real configurado, lo simula."""
    parte = SesSubmission.objects.select_for_update().get(pk=submission.pk)
    if parte.status in (SesStatus.SENT, SesStatus.ACCEPTED):
        return parte
    if parte.status == SesStatus.INCOMPLETE:
        raise SesError(_("Faltan datos: %(que)s.") % {"que": "; ".join(parte.missing) or "—"})

    parte.attempts += 1
    parte.last_attempt_at = timezone.now()
    if not settings.SES_ENABLED:
        parte.simulated = True
        parte.status = SesStatus.READY
        parte.response = str(
            _("Envío real desactivado (SES_ENABLED=False). Descarga el XML y súbelo en la sede.")
        )
        parte.save()
        return parte

    try:
        aceptado, lote, texto = _enviar(parte)
    except HttpError as exc:
        parte.status = SesStatus.ERROR
        parte.response = str(exc)
        parte.save()
        logger.warning("ses_error_de_red", submission_id=parte.pk, error=str(exc))
        return parte

    parte.simulated = False
    parte.response = texto
    parte.lot_code = lote
    if aceptado:
        parte.status = SesStatus.SENT
        parte.sent_at = timezone.now()
    else:
        parte.status = SesStatus.REJECTED
    parte.save()
    audit.record(
        AuditAction.COMPLIANCE,
        (
            _("Parte SES de %(numero)s enviado (lote %(lote)s)")
            if aceptado
            else _("Parte SES de %(numero)s rechazado")
        )
        % {"numero": parte.reservation.number, "lote": lote or "—"},
        obj=parte,
        actor=actor,
        reservation=parte.reservation,
    )
    return parte


def prepare_and_queue(*, reservation, actor=None) -> SesSubmission:
    """Al entregar el coche: prepara el parte y, si esta completo, lo encola."""
    parte = prepare(reservation=reservation, actor=actor)
    if parte.status == SesStatus.READY:
        from .tasks import send_ses_submission

        transaction.on_commit(lambda: send_ses_submission.delay(parte.pk))
    return parte
