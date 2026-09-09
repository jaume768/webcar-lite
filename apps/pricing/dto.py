"""Datos de entrada y de salida del motor de tarifas.

El motor no conoce `Reservation` y no va a conocerla: recibe un
`PriceQuoteInput` y devuelve un `PriceBreakdown`. Asi se puede pedir un precio
antes de que exista la reserva (que es justo lo que hace el mostrador cuando
alguien pregunta "y una semana cuanto me sale?") y se puede probar el motor
entero sin base de datos de reservas ni cliente HTTP.

Todos los importes son `Decimal`. Ni un float en todo el fichero.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ExtraRequest:
    """Un extra pedido, con su cantidad."""

    extra: object  # pricing.Extra
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class PriceQuoteInput:
    """Todo lo que hace falta para poner precio a un alquiler.

    `rate` se puede fijar a mano (una reserva ya creada conserva la tarifa con
    la que se vendio); si no viene, el motor la resuelve.
    """

    category: object  # fleet.VehicleCategory
    pickup_office: object  # offices.Office
    return_office: object  # offices.Office
    pickup_at: datetime
    return_at: datetime

    extras: tuple = ()
    channel: str = "counter"
    rate: object | None = None
    customer_age: int | None = None
    #: Precio por dia forzado a mano. Exige permiso en la vista que lo use.
    manual_override: Decimal | None = None
    discount_code: str = ""
    #: Minutos de cortesia. None: el valor de settings.
    courtesy_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class PriceLine:
    """Una linea del desglose.

    Se guarda base, impuesto y total **por linea**, como exige el dominio: el
    total de la reserva es la suma de las lineas, nunca un numero suelto
    recalculado aparte.
    """

    kind: str  # rental | extra | supplement | discount
    concept: str
    quantity: Decimal
    unit_price: Decimal
    base: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    #: Referencia al objeto que genero la linea (extra, suplemento, descuento).
    source_code: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "concept": self.concept,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "base": str(self.base),
            "tax_rate": str(self.tax_rate),
            "tax_amount": str(self.tax_amount),
            "total": str(self.total),
            "source_code": self.source_code,
        }


@dataclass(frozen=True, slots=True)
class PriceBreakdown:
    """Desglose completo. Es lo que se ensena en la ficha de reserva tal cual.

    `applied_rate`, `applied_tier` y `applied_season` son los objetos, para que
    la pantalla pueda escribir el nombre de la tarifa sin volver a la base de
    datos. `to_dict()` los reduce a codigos, y eso ya es serializable a JSON.
    """

    rental_days: int
    applied_rate: object | None
    applied_tier: object | None
    applied_season: object | None

    daily_price: Decimal
    base_amount: Decimal

    lines: tuple[PriceLine, ...]

    extras_total: Decimal
    supplements_total: Decimal
    discounts_total: Decimal

    taxable_base: Decimal
    tax_total: Decimal
    total: Decimal

    currency: str = "EUR"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        """Version serializable (JSON) del desglose.

        Los importes van como cadena y no como float: un float aqui seria
        justo el error que el resto del sistema se esfuerza en no cometer.
        """
        return {
            "rental_days": self.rental_days,
            "applied_rate": getattr(self.applied_rate, "code", None),
            "applied_rate_name": getattr(self.applied_rate, "name", None),
            "applied_tier": str(self.applied_tier) if self.applied_tier else None,
            "applied_season": getattr(self.applied_season, "code", None),
            "daily_price": str(self.daily_price),
            "base_amount": str(self.base_amount),
            "lines": [linea.to_dict() for linea in self.lines],
            "extras_total": str(self.extras_total),
            "supplements_total": str(self.supplements_total),
            "discounts_total": str(self.discounts_total),
            "taxable_base": str(self.taxable_base),
            "tax_total": str(self.tax_total),
            "total": str(self.total),
            "currency": self.currency,
            "warnings": list(self.warnings),
        }

    def lines_of(self, kind: str) -> tuple[PriceLine, ...]:
        return tuple(linea for linea in self.lines if linea.kind == kind)
