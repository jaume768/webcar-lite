"""Consultas que alimentan la ficha de reserva."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.auditlog.selectors import entries_for as audit_entries_for
from apps.billing.selectors import (
    deposit_held,
    has_issued_invoice,
    overpaid_amount,
    paid_amount,
    pending_amount,
)


@dataclass(frozen=True)
class HeaderData:
    """Lo que va en la cabecera fija, calculado en un solo sitio.

    La cabecera se repinta despues de cada cambio, asi que estos numeros tienen
    que salir siempre del mismo calculo que el resto de la ficha.
    """

    total: Decimal
    paid: Decimal
    pending: Decimal
    #: Fianza retenida. Va aparte a proposito: no es dinero cobrado del
    #: alquiler, es dinero del cliente que hay que devolverle.
    deposit: Decimal
    overpaid: Decimal
    invoiced: bool


def header_data(reservation) -> HeaderData:
    return HeaderData(
        total=reservation.grand_total,
        paid=paid_amount(reservation),
        pending=pending_amount(reservation),
        deposit=deposit_held(reservation),
        overpaid=overpaid_amount(reservation),
        invoiced=has_issued_invoice(reservation),
    )


@dataclass(frozen=True)
class TimelineEntry:
    """Una linea del historial, venga de donde venga."""

    happened_at: datetime
    kind: str
    title: str
    detail: str = ""
    actor: object = None
    source: str = "audit"


def timeline(reservation) -> list[TimelineEntry]:
    """Historial completo de la reserva, de lo mas reciente a lo mas antiguo.

    Junta dos fuentes: los cambios de estado, que guarda la maquina de estados,
    y la auditoria general. Mientras `auditlog` no tenga modelo, la segunda no
    aporta nada y el historial ensena solo los cambios de estado.
    """
    lineas = [
        TimelineEntry(
            happened_at=cambio.created_at,
            kind="status",
            title=f"{cambio.get_from_status_display()} → {cambio.get_to_status_display()}",
            detail=cambio.reason,
            actor=cambio.changed_by,
            source="status",
        )
        for cambio in reservation.status_changes.select_related("changed_by")
    ]

    lineas += [
        TimelineEntry(
            happened_at=cambio.created_at,
            kind="price",
            title=str(
                _("%(motivo)s: %(antes)s → %(despues)s EUR")
                % {
                    "motivo": cambio.get_kind_display(),
                    "antes": cambio.previous_total,
                    "despues": cambio.new_total,
                }
            ),
            detail=cambio.reason,
            actor=cambio.changed_by,
            source="price",
        )
        for cambio in reservation.price_changes.select_related("changed_by")
    ]

    lineas += [
        TimelineEntry(
            happened_at=apunte.happened_at,
            kind=apunte.kind,
            title=apunte.title,
            detail=apunte.detail,
            actor=apunte.actor,
            source="audit",
        )
        for apunte in audit_entries_for(reservation)
    ]

    return sorted(lineas, key=lambda linea: linea.happened_at, reverse=True)
