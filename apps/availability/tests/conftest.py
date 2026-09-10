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
def pool_baleares(db):
    return OfficePoolFactory(code="baleares", name="Baleares")


@pytest.fixture
def pool_peninsula(db):
    return OfficePoolFactory(code="peninsula", name="Peninsula")


@pytest.fixture
def palma(db, pool_baleares):
    return OfficeFactory(code="palma", name="Palma Centro", pool=pool_baleares)


@pytest.fixture
def aeropuerto(db, pool_baleares):
    """Misma isla, mismo grupo: la flota se mueve entre las dos sin friccion."""
    return OfficeFactory(code="pmi", name="Aeropuerto PMI", pool=pool_baleares)


@pytest.fixture
def valencia(db, pool_peninsula):
    """Otro grupo: un coche que acaba aqui no vuelve solo a Baleares."""
    return OfficeFactory(code="vlc", name="Valencia", pool=pool_peninsula)


@pytest.fixture
def suelta(db):
    """Oficina sin grupo: responde solo de su propia flota."""
    return OfficeFactory(code="ibiza", name="Ibiza", pool=None)


@pytest.fixture
def responsable(db, palma):
    """Usuario con permiso para cancelar reservas."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    rol = RoleFactory(
        code="responsable-disp",
        name="Responsable",
        permissions=["reservations.cancel_reservation"],
    )
    return UserFactory(email="responsable@disponibilidad.es", role=rol, offices=[palma])


@pytest.fixture
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def premium(db):
    return VehicleCategoryFactory(code="premium", name="Premium")


@pytest.fixture
def tres_coches(db, economico, palma):
    return [
        VehicleFactory(plate=f"100{i}AAA", category=economico, current_office=palma)
        for i in range(3)
    ]


@pytest.fixture
def un_coche(db, economico, palma):
    return VehicleFactory(plate="9999ZZZ", category=economico, current_office=palma)
