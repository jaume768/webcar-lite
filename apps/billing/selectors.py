"""Saldos y arqueo. Es el unico sitio que suma dinero de una reserva."""

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

CERO = Decimal("0.00")


def _pagos(reservation):
    from .models import Payment

    return Payment.objects.filter(reservation=reservation)


def paid_amount(reservation) -> Decimal:
    """Cobrado del alquiler.

    La fianza no entra: es dinero retenido que se devuelve, no un cobro. Los
    reembolsos van en negativo, asi que restan solos.
    """
    return _pagos(reservation).rental().total()


def deposit_held(reservation) -> Decimal:
    """Fianza retenida ahora mismo: lo cobrado menos lo devuelto."""
    return _pagos(reservation).deposits().total()


def pending_amount(reservation) -> Decimal:
    """Lo que falta por cobrar: alquiler mas cargos de devolucion."""
    return max(reservation.grand_total - paid_amount(reservation), CERO)


def overpaid_amount(reservation) -> Decimal:
    """Lo cobrado de mas, si es que hay. Sirve para avisar, no para cuadrar."""
    return max(paid_amount(reservation) - reservation.grand_total, CERO)


def issued_invoice_for(reservation):
    """Factura emitida de esta reserva, si la hay.

    Provisional: `Invoice` llega en su propio prompt. Cuando exista, devolver la
    factura hace que la ficha bloquee sola los cambios que la contradirian.
    """
    return None


def has_issued_invoice(reservation) -> bool:
    """Una factura emitida es inmutable: lo que ya facturo no se puede mover."""
    return issued_invoice_for(reservation) is not None


# ---------------------------------------------------------------------------
# Arqueo de caja
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CashRegister:
    """Lo que hay que cuadrar al cerrar una oficina un dia concreto."""

    office: object
    day: object
    payments: list
    cash_in: Decimal
    cash_out: Decimal

    @property
    def cash_balance(self) -> Decimal:
        """Lo que deberia haber en el cajon al cerrar."""
        return self.cash_in + self.cash_out

    @property
    def count(self) -> int:
        return len(self.payments)


def cash_register(*, office, day) -> CashRegister:
    """Arqueo de efectivo de una oficina y un dia.

    Solo efectivo: lo demas no pasa por el cajon. Entradas y salidas van
    separadas porque al cuadrar se cuentan billetes, no saldos.
    """
    from .models import Payment

    pagos = list(
        Payment.objects.cash()
        .on_day(day, office=office)
        .select_related("reservation", "created_by")
        .order_by("paid_at")
    )
    entradas = sum((pago.amount for pago in pagos if pago.amount > 0), CERO)
    salidas = sum((pago.amount for pago in pagos if pago.amount < 0), CERO)

    return CashRegister(office=office, day=day, payments=pagos, cash_in=entradas, cash_out=salidas)


def cash_totals_by_method(*, office, day) -> list[tuple[str, Decimal]]:
    """Totales por medio de pago del dia, para contrastar con el datafono."""
    from .models import Payment, PaymentMethod

    pagos = Payment.objects.on_day(day, office=office)
    totales = []
    for valor, etiqueta in PaymentMethod.choices:
        suma = pagos.filter(method=valor).total()
        if suma:
            totales.append((str(etiqueta), suma))
    return totales


def summary(reservation) -> dict:
    """Los cuatro numeros de la cabecera, calculados de una vez."""
    return {
        "total": reservation.grand_total,
        "rental_total": reservation.total,
        "charges": reservation.charges_total,
        "paid": paid_amount(reservation),
        "pending": pending_amount(reservation),
        "deposit": deposit_held(reservation),
        "overpaid": overpaid_amount(reservation),
        "invoiced": has_issued_invoice(reservation),
        "label_pending": _("Pendiente"),
    }


# ---------------------------------------------------------------------------
# Saldos en bloque
# ---------------------------------------------------------------------------


def annotate_balance(queryset):
    """Anota `cobrado` y `pendiente` sobre un queryset de reservas.

    Preguntar el pendiente reserva a reserva es una consulta por fila, y el
    panel de mostrador ensena decenas: aqui se resuelve con una subconsulta y
    la lista entera cuesta lo mismo que una.

    La fianza no entra, igual que en `paid_amount`: es dinero retenido.
    """
    from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce, Greatest

    from .models import RENTAL_TYPES, Payment

    importe = DecimalField(max_digits=10, decimal_places=2)
    cobros = (
        Payment.objects.filter(reservation=OuterRef("pk"), payment_type__in=RENTAL_TYPES)
        .values("reservation")
        .annotate(suma=Sum("amount"))
        .values("suma")
    )
    return queryset.annotate(
        cobrado=Coalesce(Subquery(cobros, output_field=importe), Value(CERO), output_field=importe),
        pendiente=Greatest(
            F("total")
            + F("charges_total")
            - Coalesce(Subquery(cobros, output_field=importe), Value(CERO), output_field=importe),
            Value(CERO),
            output_field=importe,
        ),
    )
