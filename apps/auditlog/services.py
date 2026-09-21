"""Escribir en la auditoria. Un unico punto de entrada: `record()`.

Lo llaman los servicios de cada app en el momento del hecho, dentro de su
misma transaccion: si la operacion se deshace, el apunte tambien. Asi la
auditoria no puede decir que paso algo que no llego a pasar.
"""

from decimal import Decimal

import structlog
from django.contrib.contenttypes.models import ContentType

from apps.core.middleware import get_current_user, get_request_meta

from .models import AuditLog

logger = structlog.get_logger(__name__)


def _serializable(valor):
    """Los cambios van a JSON: fechas, decimales y objetos pasan a texto."""
    if valor is None or isinstance(valor, bool | int | float | str):
        return valor
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {str(k): _serializable(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple | set):
        return [_serializable(v) for v in valor]
    return str(valor)


def _reserva_de(obj):
    """La reserva a la que pertenece el objeto, si pertenece a alguna."""
    if obj is None:
        return None
    if obj._meta.label == "reservations.Reservation":
        return obj
    return getattr(obj, "reservation", None)


def _oficina_de(obj, reserva):
    for candidato in (reserva, obj):
        if candidato is None:
            continue
        for campo in ("pickup_office_id", "office_id", "current_office_id"):
            valor = getattr(candidato, campo, None)
            if valor:
                return valor
    return None


def record(
    action: str,
    message: str,
    *,
    obj=None,
    changes: dict | None = None,
    actor=None,
    reservation=None,
    office_id: int | None = None,
) -> AuditLog:
    """Deja constancia de un hecho.

    `actor` por defecto es el usuario de la request en curso; en una tarea de
    Celery o un comando no hay request y queda como "sistema". La IP y el
    navegador salen de la request, si la hay.
    """
    actor = actor if actor is not None else get_current_user()
    if actor is not None and not getattr(actor, "pk", None):
        actor = None
    reserva = reservation or _reserva_de(obj)
    meta = get_request_meta()

    apunte = AuditLog(
        action=action,
        message=str(message)[:255],
        actor=actor,
        actor_repr=(actor.get_full_name() or actor.email) if actor else "",
        content_type=ContentType.objects.get_for_model(obj) if obj is not None else None,
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        reservation_id=getattr(reserva, "pk", None),
        office_id=office_id or _oficina_de(obj, reserva),
        changes=_serializable(changes or {}),
        ip=meta.get("ip"),
        user_agent=meta.get("user_agent", ""),
    )
    apunte.save()
    return apunte


def diff(antes: dict, despues: dict) -> dict:
    """Solo lo que cambia: `{"campo": [antes, despues]}`."""
    return {
        campo: [antes.get(campo), valor]
        for campo, valor in despues.items()
        if antes.get(campo) != valor
    }
