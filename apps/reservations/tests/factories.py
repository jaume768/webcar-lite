"""Fabricas de reservas."""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from apps.pricing.models import Extra
from apps.reservations.models import Reservation, ReservationStatus


def en(dias: float, hora: int | None = None):
    momento = timezone.now() + timedelta(days=dias)
    if hora is not None:
        momento = momento.replace(hour=hora, minute=0, second=0, microsecond=0)
    return momento


class ExtraFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Extra
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"extra-{n}")
    name = factory.Sequence(lambda n: f"Extra {n}")
    price = Decimal("10.00")
    tax_rate = Decimal("21.00")


class ReservationFactory(factory.django.DjangoModelFactory):
    """Reserva montada a mano, sin pasar por el motor ni por el alta rapida.

    Sirve para preparar escenarios. Lo que se este probando (que el alta
    respete la disponibilidad, que la transicion sea legal) se pide siempre al
    servicio correspondiente.
    """

    class Meta:
        model = Reservation
        skip_postgeneration_save = True

    pickup_at = factory.LazyFunction(lambda: en(1))
    return_at = factory.LazyFunction(lambda: en(4))
    status = ReservationStatus.CONFIRMED
    rotation_minutes = 60
    base_amount = Decimal("120.00")
    total = Decimal("145.20")
