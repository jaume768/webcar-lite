"""Fabricas de oficinas y grupos."""

import factory

from apps.accounts.tests.factories import OfficeFactory  # noqa: F401  (reexportada)
from apps.offices.models import OfficePool


class OfficePoolFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OfficePool
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"pool-{n}")
    name = factory.Sequence(lambda n: f"Grupo {n}")
