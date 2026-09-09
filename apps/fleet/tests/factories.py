"""Fabricas de flota."""

from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.tests.factories import OfficeFactory
from apps.fleet.models import (
    BlockReason,
    Fuel,
    Transmission,
    Vehicle,
    VehicleBlock,
    VehicleCategory,
    VehicleStatus,
)


class VehicleCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VehicleCategory
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"cat-{n}")
    name = factory.Sequence(lambda n: f"Categoria {n}")
    seats = 5
    doors = 5
    luggage = 2
    transmission = Transmission.MANUAL
    fuel = Fuel.PETROL
    sort_order = factory.Sequence(lambda n: 10 + n)


class VehicleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Vehicle
        django_get_or_create = ["plate"]
        skip_postgeneration_save = True

    plate = factory.Sequence(lambda n: f"{1000 + n}ABC")
    brand = "Seat"
    model = "Ibiza"
    category = factory.SubFactory(VehicleCategoryFactory)
    current_office = factory.SubFactory(OfficeFactory)
    fuel = Fuel.PETROL
    transmission = Transmission.MANUAL
    seats = 5
    status = VehicleStatus.AVAILABLE


class VehicleBlockFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VehicleBlock
        skip_postgeneration_save = True

    vehicle = factory.SubFactory(VehicleFactory)
    start_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    end_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=3))
    reason = BlockReason.WORKSHOP
