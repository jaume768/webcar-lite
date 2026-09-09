"""Propiedad del motor: la suma siempre cuadra al centimo.

No es un caso concreto, es una invariante: pase lo que pase con los dias, los
extras, los suplementos y los descuentos, `taxable_base + tax_total == total` y
los totales son la suma exacta de las lineas. Se comprueba con 1.000
combinaciones aleatorias, que es donde salen los redondeos raros que un test
escrito a mano no se le ocurren a nadie.
"""

import random
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.fleet.tests.factories import VehicleCategoryFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.dto import ExtraRequest, PriceQuoteInput
from apps.pricing.models import AmountType, CalculationType, Extra, SupplementType
from apps.pricing.services import calculate_reservation_price

from .factories import DiscountFactory, RateFactory, SupplementFactory

pytestmark = pytest.mark.django_db

#: Semilla fija: si un caso falla, se reproduce siempre igual.
SEMILLA = 20260909
COMBINACIONES = 1_000

#: Precios con decimales incomodos a proposito: los .x5 son los que destapan un
#: redondeo mal hecho (ROUND_HALF_UP frente al banquero de Python).
PRECIOS_DIA = ["19.99", "23.45", "37.335", "41.005", "50.00", "66.67"]
PRECIOS_EXTRA = ["3.33", "4.995", "5.00", "7.77", "12.505"]
IMPUESTOS = ["0.00", "4.00", "10.00", "21.00"]


@pytest.fixture
def escenario(db):
    """Un catalogo variado del que el test va tomando piezas."""
    categoria = VehicleCategoryFactory(code="eco")
    bahia = OfficePoolFactory(code="bahia")
    oficina_a = OfficeFactory(code="a", pool=bahia)
    oficina_b = OfficeFactory(code="b", pool=bahia)
    oficina_c = OfficeFactory(code="c")  # otro pool: dispara el one-way

    tarifas = {
        precio: RateFactory(
            code=f"tarifa-{precio}",
            categories=[categoria],
            tiers=[(1, 3, precio), (4, 10, precio), (11, None, precio)],
        )
        for precio in PRECIOS_DIA
    }

    extras = [
        Extra.objects.create(
            code=f"extra-{i}",
            name=f"Extra {i}",
            calculation_type=tipo,
            price=Decimal(precio),
            tax_rate=Decimal(impuesto),
            max_quantity=4,
            max_amount=Decimal("30.00") if tipo == CalculationType.PER_DAY and i % 2 else None,
        )
        for i, (tipo, precio, impuesto) in enumerate(
            [
                (CalculationType.PER_DAY, PRECIOS_EXTRA[0], IMPUESTOS[3]),
                (CalculationType.PER_DAY, PRECIOS_EXTRA[1], IMPUESTOS[1]),
                (CalculationType.ONCE, PRECIOS_EXTRA[2], IMPUESTOS[3]),
                (CalculationType.PER_RESERVATION, PRECIOS_EXTRA[3], IMPUESTOS[2]),
                (CalculationType.PER_DAY, PRECIOS_EXTRA[4], IMPUESTOS[0]),
            ]
        )
    ]

    SupplementFactory(
        code="one-way",
        supplement_type=SupplementType.ONE_WAY,
        amount=Decimal("47.55"),
        tax_rate=Decimal("21.00"),
    )
    SupplementFactory(
        code="joven",
        supplement_type=SupplementType.YOUNG_DRIVER,
        amount_type=AmountType.PERCENT,
        amount=Decimal("13.33"),
        min_age=18,
        max_age=24,
        tax_rate=Decimal("10.00"),
    )
    DiscountFactory(name="Larga", amount=Decimal("7.77"), min_days=8)

    return {
        "categoria": categoria,
        "oficinas": [oficina_a, oficina_b, oficina_c],
        "tarifas": tarifas,
        "extras": extras,
    }


def test_base_mas_impuesto_cuadra_en_mil_combinaciones(escenario):
    """Obligatorio: base + IVA == total, al centimo, siempre."""
    azar = random.Random(SEMILLA)
    salida_base = timezone.make_aware(datetime(2026, 6, 1, 9, 0))
    fallos = []

    for _ in range(COMBINACIONES):
        dias = azar.randint(1, 20)
        minutos_extra = azar.choice([0, 15, 45, 59, 60, 120])
        oficina_salida = azar.choice(escenario["oficinas"])
        oficina_vuelta = azar.choice(escenario["oficinas"])
        tarifa = escenario["tarifas"][azar.choice(PRECIOS_DIA)]
        extras = tuple(
            ExtraRequest(extra, azar.randint(1, 3))
            for extra in azar.sample(escenario["extras"], azar.randint(0, 3))
        )

        quote = PriceQuoteInput(
            category=escenario["categoria"],
            pickup_office=oficina_salida,
            return_office=oficina_vuelta,
            pickup_at=salida_base,
            return_at=salida_base + timedelta(days=dias, minutes=minutos_extra),
            extras=extras,
            rate=tarifa,
            customer_age=azar.choice([None, 19, 21, 24, 25, 30, 55]),
        )

        resultado = calculate_reservation_price(quote)

        suma_bases = sum(linea.base for linea in resultado.lines)
        suma_impuestos = sum(linea.tax_amount for linea in resultado.lines)
        suma_totales = sum(linea.total for linea in resultado.lines)

        problemas = []
        if resultado.taxable_base + resultado.tax_total != resultado.total:
            problemas.append("base + impuesto != total")
        if resultado.taxable_base != suma_bases:
            problemas.append("la base no es la suma de las lineas")
        if resultado.tax_total != suma_impuestos:
            problemas.append("el impuesto no es la suma de las lineas")
        if resultado.total != suma_totales:
            problemas.append("el total no es la suma de las lineas")
        if resultado.total != resultado.total.quantize(Decimal("0.01")):
            problemas.append("el total tiene mas de dos decimales")
        for linea in resultado.lines:
            if linea.base + linea.tax_amount != linea.total:
                problemas.append(f"la linea '{linea.concept}' no cuadra")

        if problemas:
            fallos.append((quote, resultado.to_dict(), problemas))

    assert not fallos, f"{len(fallos)} combinaciones no cuadran. Primera: {fallos[0][2]}"


def test_ninguna_combinacion_da_un_total_negativo(escenario):
    """Un descuento no puede acabar pagando el cliente al reves."""
    azar = random.Random(SEMILLA + 1)
    salida = timezone.make_aware(datetime(2026, 6, 1, 9, 0))

    for _ in range(200):
        dias = azar.randint(1, 30)
        quote = PriceQuoteInput(
            category=escenario["categoria"],
            pickup_office=escenario["oficinas"][0],
            return_office=azar.choice(escenario["oficinas"]),
            pickup_at=salida,
            return_at=salida + timedelta(days=dias),
            rate=escenario["tarifas"][azar.choice(PRECIOS_DIA)],
            customer_age=azar.choice([None, 20, 40]),
        )

        resultado = calculate_reservation_price(quote)

        assert resultado.total >= Decimal("0.00")
        assert resultado.taxable_base >= Decimal("0.00")
