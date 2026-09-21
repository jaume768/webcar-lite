"""Reglas de las politicas de la empresa. Las vistas orquestan; aqui se decide."""

import structlog
from django.db import transaction

from .models import Policy

logger = structlog.get_logger(__name__)


@transaction.atomic
def save_policy(*, policy: Policy, actor=None) -> Policy:
    """Crea o actualiza una politica ya validada por su formulario.

    Las facturas ya emitidas no cambian: guardaron su propia copia del texto.
    """
    creando = policy.pk is None
    policy.title = policy.title.strip()
    policy.save()
    logger.info(
        "politica_creada" if creando else "politica_actualizada",
        policy_id=policy.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return policy


@transaction.atomic
def set_policy_active(*, policy: Policy, active: bool, actor=None) -> Policy:
    """Alta o baja logica. Una politica desactivada deja de salir en facturas nuevas."""
    if active:
        policy.activate()
    else:
        policy.deactivate()
    logger.info(
        "politica_activada" if active else "politica_desactivada",
        policy_id=policy.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return policy
