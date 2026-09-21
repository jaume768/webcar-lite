"""Consultas de auditoria para las fichas."""

from dataclasses import dataclass
from datetime import datetime

#: Estos ya los ensena la ficha de la reserva desde sus propias tablas (cambios
#: de estado y de precio): repetirlos solo duplicaria lineas del historial.
YA_EN_LA_FICHA = ("status", "cancel", "price")


@dataclass(frozen=True)
class AuditEntry:
    """Un apunte de auditoria, en el formato que pinta la linea de tiempo."""

    happened_at: datetime
    kind: str
    title: str
    detail: str = ""
    actor: object = None


def entries_for(obj) -> list[AuditEntry]:
    """Apuntes de auditoria de un objeto, del mas reciente al mas antiguo.

    De una reserva salen tambien los de sus cobros, facturas, entregas y
    correos, que se apuntan con su `reservation_id`.
    """
    from django.contrib.contenttypes.models import ContentType

    from .models import AuditLog

    if obj is None or obj.pk is None:
        return []
    if obj._meta.label == "reservations.Reservation":
        consulta = AuditLog.objects.filter(reservation_id=obj.pk).exclude(action__in=YA_EN_LA_FICHA)
    else:
        consulta = AuditLog.objects.filter(
            content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)
        )
    return [
        AuditEntry(
            happened_at=apunte.created_at,
            kind=apunte.action,
            title=apunte.message,
            detail=apunte.get_action_display(),
            actor=apunte.actor,
        )
        for apunte in consulta.select_related("actor")[:200]
    ]
