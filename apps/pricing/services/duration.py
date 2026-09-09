"""Duracion de un alquiler en dias. **El unico sitio donde se calcula.**

Un alquiler se cobra por periodos de 24 horas con un margen de cortesia: si el
cliente se pasa menos que el margen, no se le cobra un dia mas. El margen por
defecto son 59 minutos (`settings.RENTAL_COURTESY_MINUTES`).

Ejemplos, con margen de 59 minutos:

    3 dias y 30 minutos  -> 3 dias (el exceso no llega al margen)
    3 dias y 90 minutos  -> 4 dias (el exceso lo supera)
    2 horas el mismo dia -> 1 dia  (nunca se factura cero)

Si alguna vez hace falta cambiar esta regla, se cambia aqui y en ningun otro
sitio: el precio, la disponibilidad y el contrato leen todos de esta funcion.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.utils.translation import gettext_lazy as _

DIA = timedelta(days=1)


class InvalidRentalPeriod(ValueError):
    """Las fechas no forman un alquiler: la devolucion no es posterior."""


def courtesy_margin(minutes: int | None = None) -> timedelta:
    """Margen de cortesia como timedelta. Sin argumento, el de settings."""
    if minutes is None:
        minutes = settings.RENTAL_COURTESY_MINUTES
    return timedelta(minutes=minutes)


def rental_days(
    pickup_at: datetime,
    return_at: datetime,
    *,
    courtesy_minutes: int | None = None,
) -> int:
    """Dias facturables entre dos instantes.

    Cuenta periodos completos de 24 horas y suma uno mas solo si el exceso
    supera el margen de cortesia. Nunca devuelve menos de 1: un alquiler de dos
    horas es un dia, porque el coche ha estado fuera y no se ha podido vender a
    nadie mas.
    """
    if return_at <= pickup_at:
        raise InvalidRentalPeriod(str(_("La devolucion tiene que ser posterior a la recogida.")))

    duracion = return_at - pickup_at
    dias_completos = duracion // DIA
    exceso = duracion - dias_completos * DIA

    dias = dias_completos + (1 if exceso > courtesy_margin(courtesy_minutes) else 0)
    return max(1, int(dias))
