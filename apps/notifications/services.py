"""Correos al cliente: encolar, montar en su idioma y enviar.

`queue_email` es lo unico que llaman las demas apps. Deja el apunte en el
registro y encola el envio **al confirmar la transaccion**: si la operacion
que lo provoca se deshace, el correo no sale.

El correo se monta con el idioma del cliente activo (`translation.override`):
las plantillas son las mismas y las traducciones salen de locale/.
"""

import structlog
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.html import strip_tags

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction

from .models import EmailKind, EmailLog, EmailStatus

logger = structlog.get_logger(__name__)

#: Tipo de correo -> interruptor en CompanySettings. Los de pago van siempre:
#: los pide alguien de mostrador a proposito.
INTERRUPTOR = {
    EmailKind.CONFIRMATION: "email_confirmation",
    EmailKind.REMINDER: "email_reminder",
    EmailKind.CONTRACT: "email_contract",
    EmailKind.RETURN: "email_return",
    EmailKind.INVOICE: "email_invoice",
}

IDIOMAS = ("es", "en", "de", "fr")


def _idioma(cliente) -> str:
    idioma = getattr(cliente, "language", "") or "es"
    return idioma if idioma in IDIOMAS else "es"


def queue_email(*, kind: str, reservation=None, customer=None, context=None, actor=None):
    """Deja el correo en cola. Devuelve el apunte (o None si esta desactivado)."""
    from apps.settings_app.models import CompanySettings

    interruptor = INTERRUPTOR.get(kind)
    if interruptor and not getattr(CompanySettings.load(), interruptor, True):
        return None
    cliente = customer or getattr(reservation, "customer", None)
    correo = getattr(cliente, "email", "") or ""
    apunte = EmailLog.objects.create(
        kind=kind,
        reservation=reservation,
        to_email=correo,
        language=_idioma(cliente),
        context={**(context or {}), "customer_id": getattr(cliente, "pk", None)},
        status=EmailStatus.QUEUED if correo else EmailStatus.SKIPPED,
        error="" if correo else "El cliente no tiene correo electronico.",
    )
    if correo:
        from .tasks import send_email_task

        transaction.on_commit(lambda: send_email_task.delay(apunte.pk))
    return apunte


def _contexto(apunte: EmailLog) -> dict:
    from apps.billing.models import Invoice, OnlinePayment
    from apps.customers.models import Customer
    from apps.settings_app.models import CompanySettings

    empresa = CompanySettings.load()
    reserva = apunte.reservation
    cliente = (
        reserva.customer
        if reserva is not None
        else Customer.objects.filter(pk=apunte.context.get("customer_id")).first()
    )
    contexto = {
        "empresa": empresa,
        "reserva": reserva,
        "cliente": cliente,
        "oficina": getattr(reserva, "pickup_office", None),
        "oficina_devolucion": getattr(reserva, "return_office", None),
        "base_url": settings.PUBLIC_BASE_URL,
        "logo_url": (settings.PUBLIC_BASE_URL + empresa.logo.url) if empresa.logo else "",
    }
    if apunte.context.get("pago_id"):
        contexto["pago"] = OnlinePayment.objects.filter(pk=apunte.context["pago_id"]).first()
    if apunte.context.get("invoice_id"):
        contexto["factura"] = Invoice.objects.filter(pk=apunte.context["invoice_id"]).first()
    if reserva is not None:
        from apps.billing.selectors import summary

        contexto["saldo"] = summary(reserva)
        contexto["devolucion"] = getattr(reserva, "check_out", None)
        contexto["cargos"] = list(reserva.charges.all())
    return contexto


def _adjuntos(apunte: EmailLog, contexto: dict) -> list[tuple[str, bytes, str]]:
    adjuntos = []
    if apunte.kind == EmailKind.CONTRACT and apunte.context.get("contract_id"):
        from apps.contracts.models import Contract

        contrato = Contract.objects.filter(pk=apunte.context["contract_id"]).first()
        if contrato is not None and contrato.is_ready:
            with contrato.file.open("rb") as fichero:
                adjuntos.append((contrato.filename, fichero.read(), "application/pdf"))
    factura = contexto.get("factura")
    if factura is not None:
        from apps.billing.pdf import render_invoice_pdf

        adjuntos.append((f"{factura.number}.pdf", render_invoice_pdf(factura), "application/pdf"))
    return adjuntos


def render_email(apunte: EmailLog) -> tuple[str, str, str, list]:
    """(asunto, texto, html, adjuntos) en el idioma del cliente."""
    with translation.override(apunte.language):
        contexto = _contexto(apunte)
        asunto = render_to_string(f"notifications/emails/{apunte.kind}_subject.txt", contexto)
        html = render_to_string(f"notifications/emails/{apunte.kind}.html", contexto)
        adjuntos = _adjuntos(apunte, contexto)
    asunto = " ".join(asunto.split())
    texto = "\n".join(linea.strip() for linea in strip_tags(html).splitlines() if linea.strip())
    return asunto, texto, html, adjuntos


def send(apunte_id: int) -> EmailLog:
    """Envia un correo en cola. Lanza la excepcion del backend si falla."""
    apunte = EmailLog.objects.select_related("reservation").get(pk=apunte_id)
    if apunte.status != EmailStatus.QUEUED:
        return apunte
    asunto, texto, html, adjuntos = render_email(apunte)
    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[apunte.to_email],
        reply_to=[settings.EMAIL_REPLY_TO] if settings.EMAIL_REPLY_TO else None,
    )
    mensaje.attach_alternative(html, "text/html")
    for nombre, datos, tipo in adjuntos:
        mensaje.attach(nombre, datos, tipo)

    apunte.attempts += 1
    apunte.subject = asunto[:200]
    try:
        mensaje.send()
    except Exception as exc:
        apunte.status = EmailStatus.FAILED
        apunte.error = str(exc)[:2000]
        apunte.save(update_fields=["status", "error", "attempts", "subject", "updated_at"])
        raise
    apunte.status = EmailStatus.SENT
    apunte.error = ""
    apunte.sent_at = timezone.now()
    apunte.provider_id = mensaje.extra_headers.get("X-Brevo-Message-Id", "")[:120]
    apunte.save()
    audit.record(
        AuditAction.EMAIL,
        f"{apunte.get_kind_display()} → {apunte.to_email}",
        obj=apunte,
        reservation=apunte.reservation,
        office_id=getattr(apunte.reservation, "pickup_office_id", None),
    )
    return apunte


def reservations_needing_reminder(*, now=None):
    """Reservas confirmadas que recogen dentro del plazo y aun sin recordatorio."""
    from datetime import timedelta

    from apps.reservations.models import Reservation, ReservationStatus
    from apps.settings_app.models import CompanySettings

    ahora = now or timezone.now()
    horas = CompanySettings.load().reminder_hours
    return (
        Reservation.objects.filter(
            status=ReservationStatus.CONFIRMED,
            pickup_at__gt=ahora,
            pickup_at__lte=ahora + timedelta(hours=horas),
        )
        .exclude(emails__kind=EmailKind.REMINDER)
        .select_related("customer")
    )
