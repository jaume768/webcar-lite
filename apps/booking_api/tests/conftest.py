"""Escenario: una web que vende dos oficinas de un grupo, y una tercera que no."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.models import Channel
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory


def en(dias: float, hora: int = 10):
    momento = timezone.localtime() + timedelta(days=dias)
    return momento.replace(hour=hora, minute=0, second=0, microsecond=0)


@pytest.fixture
def roles(db):
    call_command("sync_roles", verbosity=0)


@pytest.fixture
def pool(db):
    return OfficePoolFactory(code="ciudad", name="Ciudad")


@pytest.fixture
def centro(db, pool):
    return OfficeFactory(code="centro", name="Oficina Centro", pool=pool, address="Calle Mayor 1")


@pytest.fixture
def aeropuerto(db, pool):
    return OfficeFactory(code="aeropuerto", name="Aeropuerto", pool=pool)


@pytest.fixture
def norte(db):
    return OfficeFactory(code="norte", name="Oficina Norte")


@pytest.fixture
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def coche(db, economico, centro):
    return VehicleFactory(plate="1234ABC", category=economico, current_office=centro)


@pytest.fixture
def tarifa_web(db, economico):
    return RateFactory(
        code="web", name="Web", channel=Channel.WEB, categories=[economico], tiers=TRAMOS_ESTANDAR
    )


@pytest.fixture
def web(roles, centro, aeropuerto):
    """(cliente de la API, clave en claro)."""
    from apps.booking_api.services import create_client

    return create_client(name="Web principal", offices=[centro, aeropuerto])


@pytest.fixture
def api(client, web):
    """Cliente HTTP con la clave puesta."""
    _cliente, clave = web
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {clave}"
    return client


@pytest.fixture
def cuerpo():
    return {
        "category": "eco",
        "pickup_office": "centro",
        "return_office": "aeropuerto",
        "pickup_at": en(3).isoformat(),
        "return_at": en(6).isoformat(),
        "external_ref": "WEB-1",
        "customer": {
            "first_name": "Lucía",
            "last_name": "Martín Pérez",
            "email": "lucia@example.com",
            "phone": "600111222",
            "document_type": "dni",
            "document_number": "12345678Z",
            "language": "en",
        },
    }
