"""Calculo de los cargos de devolucion.

Funciones puras: reciben numeros y devuelven lineas. No tocan la base de datos
ni la request, asi que se pueden probar con una calculadora al lado.

Cada linea guarda base, impuesto y total, como el resto del dinero del sistema.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.pricing.services import redondear, rental_days
from apps.reservations.models import ChargeKind, FuelPolicy

CERO = Decimal("0.00")


@dataclass(frozen=True)
class ChargeLine:
    """Un cargo calculado, listo para guardarse como linea de la reserva."""

    kind: str
    concept: str
    quantity: Decimal
    unit_price: Decimal
    base_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal


def build_line(
    *,
    kind: str,
    concept: str,
    quantity: Decimal,
    unit_price: Decimal,
    tax_rate: Decimal,
) -> ChargeLine:
    base = redondear(Decimal(quantity) * Decimal(unit_price))
    impuesto = redondear(base * Decimal(tax_rate) / Decimal("100"))
    return ChargeLine(
        kind=kind,
        concept=concept,
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        base_amount=base,
        tax_rate=Decimal(tax_rate),
        tax_amount=impuesto,
        total=redondear(base + impuesto),
    )


def extra_km_charge(
    *,
    included_km: int | None,
    mileage_out: int,
    mileage_in: int,
    price_per_km: Decimal,
    tax_rate: Decimal,
) -> ChargeLine | None:
    """Kilometros pasados del limite incluido.

    Sin limite (kilometraje ilimitado) no hay cargo por muchos que se hagan.
    """
    if included_km is None:
        return None

    recorridos = max(mileage_in - mileage_out, 0)
    de_mas = recorridos - included_km
    if de_mas <= 0:
        return None

    return build_line(
        kind=ChargeKind.EXTRA_KM,
        concept=str(
            _("Kilometros de mas (%(de_mas)s km sobre %(incluidos)s incluidos)")
            % {"de_mas": de_mas, "incluidos": included_km}
        ),
        quantity=Decimal(de_mas),
        unit_price=price_per_km,
        tax_rate=tax_rate,
    )


def fuel_charge(
    *,
    policy: str,
    level_out: int,
    level_in: int,
    tank_liters: int,
    price_per_liter: Decimal,
    tax_rate: Decimal,
) -> ChargeLine | None:
    """Combustible que falta, segun la politica pactada.

    * Lleno-lleno y mismo nivel: se cobra lo que falte para dejarlo como salio.
    * Lleno-vacio: el cliente ya pago el deposito al recogerlo, asi que
      devolverlo vacio no cuesta nada. Y tampoco se le devuelve lo que sobre.
    """
    if policy == FuelPolicy.FULL_EMPTY:
        return None

    falta = max(level_out - level_in, 0)
    if falta == 0:
        return None

    litros = redondear(Decimal(tank_liters) * Decimal(falta) / Decimal("100"))
    if litros <= 0:
        return None

    return build_line(
        kind=ChargeKind.FUEL,
        concept=str(
            _("Combustible (%(litros)s L, del %(salida)s%% al %(entrada)s%%)")
            % {"litros": litros, "salida": level_out, "entrada": level_in}
        ),
        quantity=litros,
        unit_price=price_per_liter,
        tax_rate=tax_rate,
    )


def late_return_charge(
    *,
    pickup_at,
    planned_return_at,
    actual_return_at,
    daily_price: Decimal,
    tax_rate: Decimal,
    courtesy_minutes: int | None = None,
) -> ChargeLine | None:
    """Dias de mas por devolver tarde.

    Se cuenta con la misma regla que el alquiler (`pricing.rental_days`), que es
    la unica que sabe del margen de cortesia: un cuarto de hora tarde no es un
    dia, tres horas si.
    """
    if actual_return_at <= planned_return_at:
        return None

    facturados = rental_days(pickup_at, planned_return_at, courtesy_minutes=courtesy_minutes)
    reales = rental_days(pickup_at, actual_return_at, courtesy_minutes=courtesy_minutes)
    de_mas = reales - facturados
    if de_mas <= 0:
        return None

    return build_line(
        kind=ChargeKind.LATE_RETURN,
        concept=str(_("Devolucion tardia (%(dias)s dia(s) de mas)") % {"dias": de_mas}),
        quantity=Decimal(de_mas),
        unit_price=daily_price,
        tax_rate=tax_rate,
    )


def manual_charge(*, kind: str, concept: str, amount: Decimal, tax_rate: Decimal) -> ChargeLine:
    """Cargo escrito a mano: limpieza especial, danos, lo que sea.

    El importe que se teclea es la base imponible, como en el resto del sistema.
    """
    return build_line(
        kind=kind, concept=concept, quantity=Decimal("1"), unit_price=amount, tax_rate=tax_rate
    )
