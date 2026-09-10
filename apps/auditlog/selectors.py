"""Consultas de auditoria para las fichas.

El modelo `AuditLog` llega en su propio prompt. Hasta entonces esta funcion
devuelve una lista vacia y la pestana de historial se apoya solo en las fuentes
que ya existen (los cambios de estado de la reserva). Cuando exista el modelo,
se rellena aqui y el historial se completa solo.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditEntry:
    """Un apunte de auditoria, en el formato que pinta la linea de tiempo."""

    happened_at: datetime
    kind: str
    title: str
    detail: str = ""
    actor: object = None


def entries_for(obj) -> list[AuditEntry]:
    """Apuntes de auditoria de un objeto, del mas reciente al mas antiguo."""
    return []
