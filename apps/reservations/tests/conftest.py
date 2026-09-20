"""Escenario de mostrador: oficina, categoria, flota, tarifa y cliente."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory


@pytest.fixture(autouse=True)
def _reloj_estable():
    """Congela el "ahora" de los helpers durante cada test."""
    from .factories import fijar_referencia

    fijar_referencia(timezone.now())
    yield
    fijar_referencia(None)


@pytest.fixture
def pool(db):
    return OfficePoolFactory(code="ciudad", name="Ciudad")


@pytest.fixture
def centro(db, pool):
    return OfficeFactory(code="centro", name="Oficina Centro", pool=pool)


@pytest.fixture
def aeropuerto(db, pool):
    return OfficeFactory(code="aeropuerto", name="Aeropuerto", pool=pool)


@pytest.fixture
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def coche(db, economico, centro):
    return VehicleFactory(plate="1234ABC", category=economico, current_office=centro)


@pytest.fixture
def tarifa(db, economico, centro):
    """Tarifa de mostrador con los tramos estandar."""
    return RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[economico],
        offices=[centro],
        tiers=TRAMOS_ESTANDAR,
    )


@pytest.fixture
def cliente(db):
    return CustomerFactory(first_name="Ana", last_name="Garcia")


# --- usuarios por rol -------------------------------------------------------


@pytest.fixture
def agente(db, centro):
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
    return UserFactory(email="agente@ejemplo.es", role=rol, offices=[centro])


@pytest.fixture
def responsable(db, centro):
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
    return UserFactory(email="responsable@ejemplo.es", role=rol, offices=[centro])


@pytest.fixture
def solo_lectura(db, centro):
    """Solo lectura: no puede tocar nada."""
    rol = RoleFactory(
        code="consulta-res", name="Consulta", permissions=["reservations.view_reservation"]
    )
    return UserFactory(email="consulta@ejemplo.es", role=rol, offices=[centro])
