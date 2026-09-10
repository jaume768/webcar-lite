"""Fabricas y atajos para los tests de disponibilidad."""

from datetime import timedelta

import factory
from django.utils import timezone

from apps.reservations.models import Reservation, ReservationStatus

#: Instante de referencia del test en curso. Lo fija una fixture autouse.
#:
#: Sin esto, dos llamadas a `en(3)` dentro del mismo test devuelven instantes
#: distintos por unos microsegundos, y comparar una fecha guardada con la
#: esperada falla por ruido.
_referencia = None


def fijar_referencia(momento) -> None:
    global _referencia
    _referencia = momento


def ahora():
    return _referencia if _referencia is not None else timezone.now()


def en(dias: float, hora: int | None = None):
    """Instante a `dias` del inicio del test, o a esa hora en punto de ese dia."""
    momento = ahora() + timedelta(days=dias)
    if hora is not None:
        momento = momento.replace(hour=hora, minute=0, second=0, microsecond=0)
    return momento


class ReservationFactory(factory.django.DjangoModelFactory):
    """Crea reservas **sin pasar por el motor**.

    Sirve para montar el escenario de partida. Lo que se esta probando (que el
    motor acepte o rechace) se pide siempre a `reserve_capacity`.
    """

    class Meta:
        model = Reservation
        skip_postgeneration_save = True

    pickup_at = factory.LazyFunction(lambda: en(1))
    return_at = factory.LazyFunction(lambda: en(4))
    status = ReservationStatus.CONFIRMED
    rotation_minutes = 60
