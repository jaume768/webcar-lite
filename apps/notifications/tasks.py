"""Envio de correos en segundo plano, con reintentos si el proveedor falla."""

from celery import shared_task


@shared_task(bind=True, max_retries=4, default_retry_delay=120)
def send_email_task(self, apunte_id: int):
    from .models import EmailLog, EmailStatus
    from .services import send

    try:
        send(apunte_id)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            return  # `send` ya lo dejo como fallido, con el error anotado
        # Vuelve a la cola para el reintento; el ultimo error queda anotado.
        EmailLog.objects.filter(pk=apunte_id).update(status=EmailStatus.QUEUED)
        raise self.retry(exc=exc) from exc


@shared_task
def send_pickup_reminders():
    """Periodica: recordatorio con las instrucciones de recogida."""
    from .models import EmailKind
    from .services import queue_email, reservations_needing_reminder

    for reserva in reservations_needing_reminder():
        queue_email(kind=EmailKind.REMINDER, reservation=reserva)
