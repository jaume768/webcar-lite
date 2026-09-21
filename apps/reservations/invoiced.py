"""Una reserva con factura en vigor queda cerrada.

La factura es inmutable, asi que lo que factura tampoco se mueve: ni fechas,
ni precio, ni extras, ni vehiculo, ni conductores, ni entrega o devolucion, ni
contrato nuevo, ni cambios de estado. No hay permiso que lo salte. Para
corregirla se emite la rectificativa: al anular la factura, la reserva se
vuelve a poder tocar y se factura de nuevo.

Cobrar lo pendiente o devolver la fianza si se puede: es dinero, no la reserva.
"""

from django.utils.translation import gettext_lazy as _

from apps.billing.selectors import issued_invoice_for
from apps.core.services import ServiceError


class InvoicedReservationError(ServiceError):
    """La reserva tiene factura en vigor y no admite cambios."""


def ensure_not_invoiced(reservation) -> None:
    """Corta cualquier cambio sobre una reserva facturada."""
    factura = issued_invoice_for(reservation)
    if factura is None:
        return
    raise InvoicedReservationError(
        _(
            "La reserva %(numero)s ya está facturada (%(factura)s) y no se puede modificar. "
            "Para corregirla, emite una factura rectificativa."
        )
        % {"numero": reservation.number, "factura": factura.number}
    )


def edit_flags(reservation, user) -> dict:
    """Que puede tocar este usuario en la ficha. Solo pinta botones.

    La barrera de verdad son los servicios, que llaman a `ensure_not_invoiced`.
    """
    factura = issued_invoice_for(reservation)
    abierta = factura is None
    return {
        "facturada": factura,
        "puede_editar": abierta and user.has_perm("reservations.change_reservation"),
        "puede_editar_precio": abierta and user.has_perm("reservations.change_reservation_price"),
    }
