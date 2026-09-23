"""Exportacion de facturas y cobros para la gestoria.

El fichero lo abre una persona en Excel, casi siempre en espanol, asi que:

- separador `;` y coma decimal, que es lo que espera Excel en Espana;
- BOM al principio (`utf-8-sig`), o los acentos salen rotos;
- una fila por documento y ninguna formula: la gestoria filtra y suma.

No se inventa ningun dato. Los importes salen tal cual estan guardados, y una
factura emitida es inmutable: lo que se exporta hoy es lo que se exporto ayer.
"""

import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.billing.models import Invoice, Payment

#: Excel en espanol: punto y coma como separador de campos.
SEPARADOR = ";"


def _texto(valor) -> str:
    return "" if valor is None else str(valor)


def _numero(valor: Decimal | None) -> str:
    """Importe con coma decimal, como lo lee Excel en espanol."""
    if valor is None:
        return ""
    return f"{Decimal(valor):.2f}".replace(".", ",")


def _fecha(valor: datetime | date | None) -> str:
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        valor = timezone.localtime(valor)
    return valor.strftime("%d/%m/%Y")


def _hora(valor: datetime | None) -> str:
    return timezone.localtime(valor).strftime("%H:%M") if valor else ""


def write_csv(cabeceras: list[str], filas) -> str:
    """Vuelca las filas en un CSV con el formato acordado con la gestoria."""
    buffer = StringIO()
    escritor = csv.writer(buffer, delimiter=SEPARADOR, quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(cabeceras)
    escritor.writerows(filas)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------


def invoice_queryset(*, user, desde: date, hasta: date):
    """Facturas expedidas dentro del periodo, en el orden en que se emitieron."""
    return (
        Invoice.objects.for_user(user)
        .filter(issued_at__date__gte=desde, issued_at__date__lte=hasta)
        .select_related("series", "reservation", "office", "rectifies")
        .order_by("issued_at", "pk")
    )


def invoice_rows(facturas) -> list[list[str]]:
    return [
        [
            _texto(factura.number),
            _fecha(factura.issued_at),
            _texto(factura.series.code if factura.series_id else ""),
            _texto(factura.get_kind_display()),
            _texto(factura.customer_name),
            _texto(factura.customer_tax_id),
            _texto(factura.customer_address),
            _numero(factura.base_amount),
            _numero(factura.tax_amount),
            _numero(factura.total),
            _texto(factura.currency),
            _texto(factura.reservation.number if factura.reservation_id else ""),
            _texto(factura.office.name if factura.office_id else ""),
            _texto(factura.rectifies.number if factura.rectifies_id else ""),
            _texto(factura.hash_actual),
        ]
        for factura in facturas
    ]


def invoices_csv(*, user, desde: date, hasta: date) -> str:
    cabeceras = [
        _("Numero"),
        _("Fecha"),
        _("Serie"),
        _("Tipo"),
        _("Cliente"),
        _("NIF"),
        _("Direccion"),
        _("Base imponible"),
        _("IVA"),
        _("Total"),
        _("Moneda"),
        _("Reserva"),
        _("Oficina"),
        _("Rectifica a"),
        _("Huella"),
    ]
    return write_csv(cabeceras, invoice_rows(invoice_queryset(user=user, desde=desde, hasta=hasta)))


# ---------------------------------------------------------------------------
# Cobros
# ---------------------------------------------------------------------------


def payment_queryset(*, user, desde: date, hasta: date):
    return (
        Payment.objects.for_user(user)
        .filter(paid_at__date__gte=desde, paid_at__date__lte=hasta)
        .select_related("reservation", "reservation__customer", "office", "created_by")
        .order_by("paid_at", "pk")
    )


def payment_rows(cobros) -> list[list[str]]:
    filas = []
    for cobro in cobros:
        reserva = cobro.reservation if cobro.reservation_id else None
        cliente = reserva.customer if reserva and reserva.customer_id else None
        filas.append(
            [
                _fecha(cobro.paid_at),
                _hora(cobro.paid_at),
                _texto(reserva.number if reserva else ""),
                _texto(cliente.full_name if cliente else ""),
                _texto(cliente.document_number if cliente else ""),
                _texto(cobro.get_payment_type_display()),
                _texto(cobro.get_method_display()),
                _numero(cobro.amount),
                _texto(cobro.reference),
                _texto(cobro.office.name if cobro.office_id else ""),
                _texto(cobro.created_by.get_short_name() if cobro.created_by_id else ""),
            ]
        )
    return filas


def payments_csv(*, user, desde: date, hasta: date) -> str:
    cabeceras = [
        _("Fecha"),
        _("Hora"),
        _("Reserva"),
        _("Cliente"),
        _("Documento"),
        _("Concepto"),
        _("Medio de pago"),
        _("Importe"),
        _("Referencia"),
        _("Oficina"),
        _("Usuario"),
    ]
    return write_csv(cabeceras, payment_rows(payment_queryset(user=user, desde=desde, hasta=hasta)))


def filename(prefijo: str, desde: date, hasta: date) -> str:
    return f"{prefijo}-{desde.isoformat()}-{hasta.isoformat()}.csv"
