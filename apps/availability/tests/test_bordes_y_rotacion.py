"""Solapes por los bordes y tiempo de rotacion.

Es donde se cuela el error clasico: dar por libre un coche que todavia se esta
limpiando, o rechazar dos alquileres que en realidad encajan.
"""

from datetime import timedelta

import pytest

from apps.availability.services import check_category_availability, occupancy_range
from apps.reservations.models import ReservationStatus

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db


def _reserva_de(economico, palma, inicio, fin, rotacion):
    return ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        pickup_at=inicio,
        return_at=fin,
        rotation_minutes=rotacion,
        status=ReservationStatus.CONFIRMED,
    )


def test_pegadas_sin_rotacion_no_chocan(economico, palma, un_coche):
    """A termina a las 10:00 y B empieza a las 10:00: el rango es [inicio, fin)."""
    _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=0)

    resultado = check_category_availability(
        economico, palma, en(1, hora=10), en(1, hora=14), rotation_minutes=0
    )

    assert resultado.available
    assert resultado.reserved == 0


def test_pegadas_con_rotacion_de_una_hora_chocan(economico, palma, un_coche):
    """El coche devuelto a las 10:00 no esta listo hasta las 11:00."""
    _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=60)

    resultado = check_category_availability(
        economico, palma, en(1, hora=10), en(1, hora=14), rotation_minutes=60
    )

    assert not resultado.available
    assert resultado.reserved == 1


def test_con_el_hueco_justo_de_rotacion_encajan(economico, palma, un_coche):
    _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=60)

    resultado = check_category_availability(
        economico, palma, en(1, hora=11), en(1, hora=14), rotation_minutes=60
    )

    assert resultado.available


def test_un_minuto_antes_de_la_rotacion_todavia_choca(economico, palma, un_coche):
    _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=60)
    casi = en(1, hora=11) - timedelta(minutes=1)

    resultado = check_category_availability(economico, palma, casi, en(1, hora=14))

    assert not resultado.available


def test_la_rotacion_tambien_protege_por_delante(economico, palma, un_coche):
    """Lo que se pide tambien necesita su limpieza: no vale acabar pegado."""
    _reserva_de(economico, palma, en(1, hora=12), en(1, hora=18), rotacion=60)

    # Termina a las 11:30 y necesita hasta las 12:30, que ya pisa la otra.
    resultado = check_category_availability(
        economico, palma, en(1, hora=8), en(1, hora=11).replace(minute=30)
    )

    assert not resultado.available


def test_periodos_que_solo_se_tocan_por_fuera_no_estorban(economico, palma, un_coche):
    _reserva_de(economico, palma, en(10), en(12), rotacion=60)

    assert check_category_availability(economico, palma, en(1), en(3)).available


def test_cada_reserva_guarda_su_propia_rotacion(economico, palma, un_coche):
    """Cambiar el ajuste general no puede mover la ocupacion de lo ya vendido."""
    reserva = _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=90)
    reserva.refresh_from_db()

    esperado = occupancy_range(en(1, hora=8), en(1, hora=10), 90)
    assert reserva.occupancy_period.upper == esperado.upper
    assert reserva.rotation_minutes == 90


def test_la_columna_generada_sigue_a_las_fechas(economico, palma, un_coche):
    """`occupancy_period` lo calcula Postgres: no puede quedarse desalineado."""
    reserva = _reserva_de(economico, palma, en(1, hora=8), en(1, hora=10), rotacion=60)

    reserva.return_at = en(2, hora=10)
    reserva.save()
    reserva.refresh_from_db()

    assert reserva.occupancy_period.upper == en(2, hora=11)
