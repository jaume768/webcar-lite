"""Fabricas de clientes."""

import factory

from apps.customers.models import Customer, DocumentType

LETRAS_CONTROL = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni_valido(numero: int) -> str:
    """DNI con su letra de control: los tests no pueden usar documentos falsos."""
    base = numero % 100000000
    return f"{base:08d}{LETRAS_CONTROL[base % 23]}"


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer
        skip_postgeneration_save = True

    first_name = factory.Sequence(lambda n: f"Nombre{n}")
    last_name = factory.Sequence(lambda n: f"Apellido{n}")
    document_type = DocumentType.DNI
    document_number = factory.Sequence(dni_valido)
    email = factory.Sequence(lambda n: f"cliente{n}@ejemplo.es")
    phone = factory.Sequence(lambda n: f"6{n:08d}")
    birth_date = factory.Faker("date_of_birth", minimum_age=20, maximum_age=70)
