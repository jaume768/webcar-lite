"""Informes de explotacion: ingresos por coche y ocupacion por mes.

El nucleo del calculo se prueba sin base de datos: reparte dias entre meses y
suma euros, y eso no necesita ni una sola consulta.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.reports import services

# ---------------------------------------------------------------------------
# Calculo puro
# ---------------------------------------------------------------------------


def test_los_meses_del_rango_salen_completos():
    meses = services.months_between(date(2026, 11, 20), date(2027, 2, 3))

    assert meses == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


def test_un_alquiler_a_caballo_de_dos_meses_se_reparte():
    """Sin repartir, la ocupacion de cada mes no querria decir nada."""
    meses = [date(2026, 6, 1), date(2026, 7, 1)]

    filas = services.occupancy_rows(
        meses=meses, vehiculos=1, periodos=[(date(2026, 6, 28), date(2026, 7, 2))]
    )

    junio, julio = filas
    assert junio.rented_days == 3  # 28, 29 y 30 de junio
    assert julio.rented_days == 2  # 1 y 2 de julio
    assert junio.available_days == 30
    assert julio.available_days == 31


def test_la_ocupacion_es_un_porcentaje_de_dias_de_flota():
    filas = services.occupancy_rows(
        meses=[date(2026, 4, 1)],
        vehiculos=2,
        periodos=[(date(2026, 4, 1), date(2026, 4, 15))],
    )

    # 15 dias de 60 posibles (2 coches x 30 dias).
    assert filas[0].rented_days == 15
    assert filas[0].available_days == 60
    assert filas[0].rate == Decimal("25.0")


def test_sin_flota_la_ocupacion_no_revienta():
    filas = services.occupancy_rows(meses=[date(2026, 4, 1)], vehiculos=0, periodos=[])

    assert filas[0].rate == Decimal("0.00")


def test_los_ingresos_se_agrupan_por_coche_y_se_ordenan():
    filas = services.revenue_rows(
        [
            {
                "vehicle_id": 1,
                "plate": "1111AAA",
                "label": "Seat Ibiza",
                "category": "Economico",
                "days": 3,
                "revenue": Decimal("150.00"),
            },
            {
                "vehicle_id": 1,
                "plate": "1111AAA",
                "label": "Seat Ibiza",
                "category": "Economico",
                "days": 2,
                "revenue": Decimal("100.00"),
            },
            {
                "vehicle_id": 2,
                "plate": "2222BBB",
                "label": "Kia Niro",
                "category": "Familiar",
                "days": 10,
                "revenue": Decimal("600.00"),
            },
        ]
    )

    primero, segundo = filas
    assert primero.plate == "2222BBB"  # el que mas factura, arriba
    assert segundo.reservations == 2
    assert segundo.days == 5
    assert segundo.revenue == Decimal("250.00")
    assert segundo.revenue_per_day == Decimal("50.00")


def test_un_coche_sin_dias_no_divide_entre_cero():
    fila = services.VehicleRevenue(
        vehicle_id=1,
        plate="1111AAA",
        label="Seat Ibiza",
        category="Economico",
        reservations=0,
        days=0,
        revenue=Decimal("0.00"),
    )

    assert fila.revenue_per_day == Decimal("0.00")


@pytest.mark.parametrize(
    ("horas", "esperado"),
    [(2, 1), (24, 1), (25, 2), (72, 3), (73, 4)],
)
def test_un_dia_empezado_cuenta_como_dia(horas, esperado):
    from datetime import datetime, timedelta

    inicio = datetime(2026, 5, 1, 10, 0)

    assert services.rented_days(inicio, inicio + timedelta(hours=horas)) == esperado


def test_el_periodo_por_defecto_cubre_doce_meses():
    desde, hasta = services.default_range("ano", hoy=date(2026, 9, 23))

    assert desde == date(2025, 10, 1)
    assert hasta == date(2026, 9, 23)


def test_el_periodo_del_mes_empieza_el_dia_uno():
    desde, hasta = services.default_range("mes", hoy=date(2026, 9, 23))

    assert (desde, hasta) == (date(2026, 9, 1), date(2026, 9, 23))
