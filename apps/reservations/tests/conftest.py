"""Escenario de mostrador: oficina, categoria, flota, tarifa y cliente."""

import pytest

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory


@pytest.fixture
def pool(db):
    return OfficePoolFactory(code="baleares", name="Baleares")


@pytest.fixture
def palma(db, pool):
    return OfficeFactory(code="palma", name="Palma Centro", pool=pool)


@pytest.fixture
def aeropuerto(db, pool):
    return OfficeFactory(code="pmi", name="Aeropuerto PMI", pool=pool)


@pytest.fixture
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def coche(db, economico, palma):
    return VehicleFactory(plate="1234ABC", category=economico, current_office=palma)


@pytest.fixture
def tarifa(db, economico, palma):
    """Tarifa de mostrador con los tramos estandar."""
    return RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[economico],
        offices=[palma],
        tiers=TRAMOS_ESTANDAR,
    )


@pytest.fixture
def cliente(db):
    return CustomerFactory(first_name="Ana", last_name="Garcia")


# --- usuarios por rol -------------------------------------------------------


@pytest.fixture
def agente(db, palma):
    """Mostrador: crea y edita reservas, pero no cancela."""
    rol = RoleFactory(
        code="mostrador-res",
        name="Mostrador",
        permissions=[
            "reservations.view_reservation",
            "reservations.add_reservation",
            "reservations.change_reservation",
        ],
    )
    return UserFactory(email="agente@ejemplo.es", role=rol, offices=[palma])


@pytest.fixture
def responsable(db, palma):
    """Responsable: ademas puede cancelar."""
    rol = RoleFactory(
        code="responsable-res",
        name="Responsable",
        permissions=[
            "reservations.view_reservation",
            "reservations.add_reservation",
            "reservations.change_reservation",
            "reservations.cancel_reservation",
        ],
    )
    return UserFactory(email="responsable@ejemplo.es", role=rol, offices=[palma])


@pytest.fixture
def solo_lectura(db, palma):
    """Solo lectura: no puede tocar nada."""
    rol = RoleFactory(
        code="consulta-res", name="Consulta", permissions=["reservations.view_reservation"]
    )
    return UserFactory(email="consulta@ejemplo.es", role=rol, offices=[palma])
