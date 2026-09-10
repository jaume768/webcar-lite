"""Tareas de Celery de contratos."""

import structlog
from celery import shared_task

from .models import Contract

logger = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def generate_contract(self, contract_id: int) -> int:
    """Genera el PDF de un contrato.

    Va fuera de la request porque WeasyPrint tarda lo suyo y el mostrador tiene
    a alguien delante. Si falla, se reintenta: la fila queda en estado de fallo
    con el motivo a la vista.
    """
    from .services import build_contract

    contrato = Contract.objects.select_related(
        "reservation__customer",
        "reservation__vehicle",
        "reservation__category",
        "reservation__pickup_office",
        "reservation__return_office",
        "terms_version",
    ).get(pk=contract_id)

    build_contract(contrato)
    return contrato.pk
