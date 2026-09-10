"""Lo que la ficha de reserva necesita saber de facturacion.

Facturas y cobros llegan en su propio prompt. Este modulo existe para que el
resto del sistema pueda preguntar ya por lo cobrado y lo facturado sin inventar
numeros ni esparcir condicionales: hoy responde lo unico honesto (nada cobrado,
nada facturado) y cuando existan `Payment` e `Invoice` se cambia **aqui**, en un
sitio, y la ficha no se entera.
"""

from decimal import Decimal

CERO = Decimal("0.00")


def paid_amount(reservation) -> Decimal:
    """Cuanto se ha cobrado ya de esta reserva.

    Provisional: sin modelo `Payment` todavia no hay cobros que sumar.
    """
    return CERO


def pending_amount(reservation) -> Decimal:
    """Lo que falta por cobrar. Nunca negativo."""
    return max(reservation.total - paid_amount(reservation), CERO)


def issued_invoice_for(reservation):
    """Factura emitida de esta reserva, si la hay.

    Provisional: sin modelo `Invoice` no hay ninguna. Cuando exista, devolver la
    factura hace que la ficha bloquee sola los cambios que la contradirian.
    """
    return None


def has_issued_invoice(reservation) -> bool:
    """Una factura emitida es inmutable: lo que ya facturo no se puede mover.

    Quien tenga `reservations.change_invoiced_reservation` puede saltarselo, y
    entonces le toca emitir la rectificativa correspondiente.
    """
    return issued_invoice_for(reservation) is not None
