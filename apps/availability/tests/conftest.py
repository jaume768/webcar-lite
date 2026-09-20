"""Escenario base: un grupo con dos oficinas y una categoria con tres coches."""

import pytest
from django.utils import timezone

from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory


@pytest.fixture(autouse=True)
def _reloj_estable():
    """Congela el "ahora" de los helpers durante cada test."""
    from .factories import fijar_referencia

    fijar_referencia(timezone.now())
    yield
    fijar_referencia(None)


@pytest.fixture
def pool_ciudad(db):
    return OfficePoolFactory(code="ciudad", name="Ciudad")


@pytest.fixture
def pool_lejano(db):
    return OfficePoolFactory(code="otra-zona", name="Otra zona")


@pytest.fixture
def centro(db, pool_ciudad):
    return OfficeFactory(code="centro", name="Oficina Centro", pool=pool_ciudad)


@pytest.fixture
def aeropuerto(db, pool_ciudad):
    """Misma zona, mismo grupo: la flota se mueve entre las dos sin friccion."""
    return OfficeFactory(code="aeropuerto", name="Aeropuerto", pool=pool_ciudad)


@pytest.fixture
def lejana(db, pool_lejano):
    """Otro grupo: un coche que acaba aqui no vuelve solo a la ciudad."""
    return OfficeFactory(code="lejana", name="Oficina Lejana", pool=pool_lejano)


@pytest.fixture
def suelta(db):
    """Oficina sin grupo: responde solo de su propia flota."""
    return OfficeFactory(code="suelta", name="Oficina Suelta", pool=None)


@pytest.fixture
def responsable(db, centro):
    """Usuario con permiso para cancelar reservas."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    rol = RoleFactory(
        code="responsable-disp",
        name="Responsable",
        permissions=["reservations.cancel_reservation"],
    )
    return UserFactory(email="responsable@disponibilidad.es", role=rol, offices=[centro])


@pytest.fixture
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def premium(db):
    return VehicleCategoryFactory(code="premium", name="Premium")


@pytest.fixture
def tres_coches(db, economico, centro):
    return [
        VehicleFactory(plate=f"100{i}AAA", category=economico, current_office=centro)
        for i in range(3)
    ]


@pytest.fixture
def un_coche(db, economico, centro):
    return VehicleFactory(plate="9999ZZZ", category=economico, current_office=centro)
