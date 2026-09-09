"""Fabricas de tarifas."""

from decimal import Decimal

import factory

from apps.pricing.models import (
    AmountType,
    Channel,
    Discount,
    Rate,
    RateTier,
    Season,
    Supplement,
    SupplementType,
    TierMode,
)

#: Los tramos del enunciado: 1 / 2-3 / 4-7 / 8-14 / 15+ a 50/45/40/35/30.
TRAMOS_ESTANDAR = [
    (1, 1, "50.00"),
    (2, 3, "45.00"),
    (4, 7, "40.00"),
    (8, 14, "35.00"),
    (15, None, "30.00"),
]


class SeasonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Season
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"temporada-{n}")
    name = factory.Sequence(lambda n: f"Temporada {n}")
    priority = 0


class RateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Rate
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"tarifa-{n}")
    name = factory.Sequence(lambda n: f"Tarifa {n}")
    channel = Channel.COUNTER
    tier_mode = TierMode.FLAT
    priority = 0

    @factory.post_generation
    def categories(self, create, extracted, **kwargs):
        if create and extracted:
            self.categories.set(extracted)

    @factory.post_generation
    def offices(self, create, extracted, **kwargs):
        if create and extracted:
            self.offices.set(extracted)

    @factory.post_generation
    def tiers(self, create, extracted, **kwargs):
        """`tiers=TRAMOS_ESTANDAR` crea los tramos de una vez."""
        if not create or extracted is None:
            return
        for min_days, max_days, precio in extracted:
            RateTier.objects.create(
                rate=self,
                min_days=min_days,
                max_days=max_days,
                price_per_day=Decimal(precio),
            )


class SupplementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplement
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"suplemento-{n}")
    name = factory.Sequence(lambda n: f"Suplemento {n}")
    supplement_type = SupplementType.ONE_WAY
    amount_type = AmountType.FIXED
    amount = Decimal("50.00")
    tax_rate = Decimal("21.00")

    @factory.post_generation
    def offices(self, create, extracted, **kwargs):
        if create and extracted:
            self.offices.set(extracted)


class DiscountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Discount
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Descuento {n}")
    amount_type = AmountType.PERCENT
    amount = Decimal("10.00")

    @factory.post_generation
    def categories(self, create, extracted, **kwargs):
        if create and extracted:
            self.categories.set(extracted)
