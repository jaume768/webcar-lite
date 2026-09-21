"""Envio de partes a SES.Hospedajes en segundo plano."""

import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def send_ses_submission(self, submission_id: int):
    from .models import SesStatus, SesSubmission
    from .services import send

    parte = SesSubmission.objects.filter(pk=submission_id).first()
    if parte is None:
        return
    parte = send(submission=parte)
    if parte.status == SesStatus.ERROR:
        # Red caida o servicio sin responder: se reintenta con espera.
        raise self.retry()


@shared_task
def retry_pending_ses():
    """Periodica: rehace lo incompleto (por si ya se completo) y reenvia lo listo."""
    from .models import SesStatus, SesSubmission
    from .services import prepare

    for parte in SesSubmission.objects.pending().select_related("reservation"):
        if parte.status in (SesStatus.INCOMPLETE, SesStatus.ERROR, SesStatus.READY):
            parte = prepare(reservation=parte.reservation)
            if parte.status == SesStatus.READY and not parte.simulated:
                send_ses_submission.delay(parte.pk)
