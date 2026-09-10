"""Calculo de cargos. Sin base de datos: son funciones puras."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.operations.charges import extra_km_charge, fuel_charge, late_return_charge
from apps.reservations.models import FuelPolicy

SIN_IVA = Decimal("0.00")


# --- kilometros -------------------------------------------------------------


def test_km_de_mas_al_precio_configurado():
    """1.200 km recorridos, 1.000 incluidos (100/dia x 10 dias), 0,15 EUR/km."""
    linea = extra_km_charge(
        included_km=1000,
        mileage_out=10_000,
        mileage_in=11_200,
        price_per_km=Decimal("0.15"),
        tax_rate=SIN_IVA,
    )

    assert linea.quantity == Decimal("200")
    assert linea.unit_price == Decimal("0.15")
    assert linea.total == Decimal("30.00")


def test_sin_pasarse_no_hay_cargo():
    assert (
        extra_km_charge(
            included_km=1000,
            mileage_out=10_000,
            mileage_in=10_800,
            price_per_km=Decimal("0.15"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_justo_en_el_limite_no_hay_cargo():
    assert (
        extra_km_charge(
            included_km=1000,
            mileage_out=10_000,
            mileage_in=11_000,
            price_per_km=Decimal("0.15"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_kilometraje_ilimitado_no_cobra_nunca():
    assert (
        extra_km_charge(
            included_km=None,
            mileage_out=10_000,
            mileage_in=99_000,
            price_per_km=Decimal("0.15"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_el_cargo_de_km_lleva_su_impuesto():
    linea = extra_km_charge(
        included_km=1000,
        mileage_out=10_000,
        mileage_in=11_200,
        price_per_km=Decimal("0.15"),
        tax_rate=Decimal("21.00"),
    )

    assert linea.base_amount == Decimal("30.00")
    assert linea.tax_amount == Decimal("6.30")
    assert linea.total == Decimal("36.30")


# --- combustible ------------------------------------------------------------


def test_lleno_lleno_cobra_lo_que_falta():
    """Sale lleno, vuelve a la mitad: 25 L de 50, a 1,60 EUR."""
    linea = fuel_charge(
        policy=FuelPolicy.FULL_FULL,
        level_out=100,
        level_in=50,
        tank_liters=50,
        price_per_liter=Decimal("1.60"),
        tax_rate=SIN_IVA,
    )

    assert linea.quantity == Decimal("25.00")
    assert linea.total == Decimal("40.00")


def test_devolviendolo_lleno_no_se_cobra():
    assert (
        fuel_charge(
            policy=FuelPolicy.FULL_FULL,
            level_out=100,
            level_in=100,
            tank_liters=50,
            price_per_liter=Decimal("1.60"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_lleno_vacio_no_cobra_combustible():
    """El deposito ya se pago al recogerlo."""
    assert (
        fuel_charge(
            policy=FuelPolicy.FULL_EMPTY,
            level_out=100,
            level_in=0,
            tank_liters=50,
            price_per_liter=Decimal("1.60"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_devolverlo_con_mas_gasolina_no_da_dinero():
    assert (
        fuel_charge(
            policy=FuelPolicy.FULL_FULL,
            level_out=50,
            level_in=100,
            tank_liters=50,
            price_per_liter=Decimal("1.60"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_mismo_nivel_cobra_la_diferencia():
    linea = fuel_charge(
        policy=FuelPolicy.SAME_LEVEL,
        level_out=75,
        level_in=25,
        tank_liters=60,
        price_per_liter=Decimal("1.50"),
        tax_rate=SIN_IVA,
    )

    assert linea.quantity == Decimal("30.00")
    assert linea.total == Decimal("45.00")


# --- devolucion tardia ------------------------------------------------------


def _periodo(horas_tarde):
    recogida = timezone.now()
    prevista = recogida + timedelta(days=3)
    return recogida, prevista, prevista + timedelta(hours=horas_tarde)


def test_tres_horas_tarde_con_margen_de_59_minutos_es_un_dia():
    """El criterio de aceptacion, tal cual."""
    recogida, prevista, real = _periodo(3)

    linea = late_return_charge(
        pickup_at=recogida,
        planned_return_at=prevista,
        actual_return_at=real,
        daily_price=Decimal("45.00"),
        tax_rate=SIN_IVA,
        courtesy_minutes=59,
    )

    assert linea.quantity == Decimal("1")
    assert linea.total == Decimal("45.00")


def test_media_hora_tarde_entra_en_la_cortesia():
    recogida, prevista, real = _periodo(0.5)

    assert (
        late_return_charge(
            pickup_at=recogida,
            planned_return_at=prevista,
            actual_return_at=real,
            daily_price=Decimal("45.00"),
            tax_rate=SIN_IVA,
            courtesy_minutes=59,
        )
        is None
    )


def test_devolver_a_la_hora_no_cobra_nada():
    recogida, prevista, _real = _periodo(0)

    assert (
        late_return_charge(
            pickup_at=recogida,
            planned_return_at=prevista,
            actual_return_at=prevista,
            daily_price=Decimal("45.00"),
            tax_rate=SIN_IVA,
        )
        is None
    )


def test_devolver_dos_dias_tarde_cobra_dos_dias():
    recogida, prevista, real = _periodo(48)

    linea = late_return_charge(
        pickup_at=recogida,
        planned_return_at=prevista,
        actual_return_at=real,
        daily_price=Decimal("45.00"),
        tax_rate=SIN_IVA,
        courtesy_minutes=59,
    )

    assert linea.quantity == Decimal("2")
    assert linea.total == Decimal("90.00")


@pytest.mark.parametrize("adelanto", [1, 12, 48])
def test_devolver_antes_no_descuenta(adelanto):
    recogida, prevista, _real = _periodo(0)

    assert (
        late_return_charge(
            pickup_at=recogida,
            planned_return_at=prevista,
            actual_return_at=prevista - timedelta(hours=adelanto),
            daily_price=Decimal("45.00"),
            tax_rate=SIN_IVA,
        )
        is None
    )
