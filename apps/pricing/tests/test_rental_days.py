"""Duracion facturable. No toca la base de datos: es aritmetica pura."""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.pricing.services import InvalidRentalPeriod, rental_days


def momento(dia=1, hora=10, minuto=0):
    return timezone.make_aware(datetime(2026, 6, dia, hora, minuto))


def test_tres_dias_y_treinta_minutos_son_tres_dias():
    """Obligatorio: el exceso no llega al margen de cortesia de 59 minutos."""
    salida = momento()

    assert rental_days(salida, salida + timedelta(days=3, minutes=30), courtesy_minutes=59) == 3


def test_tres_dias_y_noventa_minutos_son_cuatro_dias():
    """Obligatorio: el exceso supera el margen."""
    salida = momento()

    assert rental_days(salida, salida + timedelta(days=3, minutes=90), courtesy_minutes=59) == 4


def test_el_mismo_dia_a_dos_horas_es_un_dia():
    """Obligatorio: el coche ha estado fuera, no se factura cero."""
    salida = momento(hora=9)

    assert rental_days(salida, momento(hora=11)) == 1


def test_el_exceso_justo_en_el_margen_no_suma_dia():
    """59 minutos exactos entran; el minuto 60 ya no."""
    salida = momento()

    assert rental_days(salida, salida + timedelta(days=1, minutes=59), courtesy_minutes=59) == 1
    assert rental_days(salida, salida + timedelta(days=1, minutes=60), courtesy_minutes=59) == 2


def test_veinticuatro_horas_clavadas_son_un_dia():
    salida = momento()

    assert rental_days(salida, salida + timedelta(days=1)) == 1


def test_el_margen_es_configurable(settings):
    """Cambiar el margen en settings cambia la duracion, sin tocar codigo."""
    salida = momento()
    devolucion = salida + timedelta(days=2, minutes=45)

    assert rental_days(salida, devolucion, courtesy_minutes=59) == 2
    assert rental_days(salida, devolucion, courtesy_minutes=30) == 3

    settings.RENTAL_COURTESY_MINUTES = 30
    assert rental_days(salida, devolucion) == 3


@pytest.mark.parametrize(
    "desplazamiento", [timedelta(0), timedelta(minutes=-1), timedelta(days=-2)]
)
def test_una_devolucion_que_no_es_posterior_no_es_un_alquiler(desplazamiento):
    salida = momento()

    with pytest.raises(InvalidRentalPeriod):
        rental_days(salida, salida + desplazamiento)


def test_dos_semanas_largas():
    salida = momento()

    assert rental_days(salida, salida + timedelta(days=15, hours=3)) == 16
