"""Los documentos de una reserva, vengan de donde vengan."""

from dataclasses import dataclass
from datetime import datetime

from django.urls import reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class DocumentEntry:
    """Una fila de la pestana de documentos."""

    kind: str
    title: str
    created_at: datetime | None
    download_url: str = ""
    status: str = "ready"
    detail: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and bool(self.download_url)


def documents_for(reservation) -> list[DocumentEntry]:
    """Contratos, facturas, partes de danos y copias de documentos.

    Todo lo descargable de una reserva en una sola lista, para que la pestana no
    tenga que saber de que app sale cada cosa.
    """
    entradas: list[DocumentEntry] = []

    for contrato in reservation.contracts.all():
        entradas.append(
            DocumentEntry(
                kind="contract",
                title=str(
                    _("Contrato de alquiler (condiciones v%(version)s)")
                    % {"version": contrato.terms_version.version}
                ),
                created_at=contrato.generated_at or contrato.created_at,
                download_url=(
                    reverse("contracts:download", args=[reservation.pk, contrato.pk])
                    if contrato.is_ready
                    else ""
                ),
                status=contrato.status,
                detail=contrato.error,
            )
        )

    for dano in reservation.damages.prefetch_related("photos"):
        for foto in dano.photos.all():
            entradas.append(
                DocumentEntry(
                    kind="damage_photo",
                    title=str(_("Parte de danos: %(zona)s") % {"zona": dano.get_zone_display()}),
                    created_at=foto.created_at,
                    download_url=reverse("contracts:damage_photo", args=[reservation.pk, foto.pk]),
                    detail=foto.caption,
                )
            )

    if reservation.customer_id:
        for documento in reservation.customer.documents.all():
            entradas.append(
                DocumentEntry(
                    kind="customer_document",
                    title=str(_("%(tipo)s del cliente") % {"tipo": documento.get_kind_display()}),
                    created_at=documento.created_at,
                    download_url=reverse("customers:document_download", args=[documento.pk]),
                    detail=documento.notes,
                )
            )

    entradas.extend(_facturas(reservation))
    return sorted(entradas, key=lambda entrada: entrada.created_at or datetime.min, reverse=True)


def _facturas(reservation) -> list[DocumentEntry]:
    """Facturas de la reserva, rectificativas incluidas. El PDF se genera al pedirlo."""
    from apps.billing.selectors import invoices_of

    return [
        DocumentEntry(
            kind="invoice",
            title=str(
                _("Factura rectificativa %(numero)s")
                if factura.is_rectifying
                else _("Factura %(numero)s")
            )
            % {"numero": factura.number},
            created_at=factura.issued_at,
            download_url=reverse("billing:invoice_pdf", args=[factura.pk]),
            detail=f"{factura.total} €",
        )
        for factura in invoices_of(reservation)
    ]
