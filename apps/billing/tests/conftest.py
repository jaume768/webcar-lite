"""Escenario de cobros: una reserva de 163,35 EUR y usuarios por rol."""

import pytest

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory
from apps.reservations.tests.factories import en, fijar_referencia


@pytest.fixture(autouse=True)
def _reloj_estable():
    from django.utils import timezone

    fijar_referencia(timezone.now())
    yield
    fijar_referencia(None)


@pytest.fixture
def centro(db):
    return OfficeFactory(code="centro", name="Oficina Centro", pool=OfficePoolFactory(code="bal"))


@pytest.fixture
def norte(db, centro):
    return OfficeFactory(code="norte", name="Oficina Norte", pool=centro.pool)


@pytest.fixture
def reserva(db, centro):
    from apps.reservations.services import create_quick_reservation

    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    VehicleFactory(plate="1234ABC", category=categoria, current_office=centro)
    RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[categoria],
        offices=[centro],
        tiers=TRAMOS_ESTANDAR,
    )
    return create_quick_reservation(
        category=categoria,
        pickup_office=centro,
        customer=CustomerFactory(),
        pickup_at=en(1),
        return_at=en(4),
    )


@pytest.fixture
def cajero(db, centro):
    """Mostrador: cobra, pero no puede cobrar de mas."""
    return UserFactory(
        email="cajero@ejemplo.es",
        role=RoleFactory(
            code="cajero",
            name="Cajero",
            permissions=[
                "reservations.view_reservation",
                "billing.add_payment",
                "billing.view_billing",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def responsable(db, centro):
    """Ademas puede autorizar cobros por encima del pendiente."""
    return UserFactory(
        email="responsable@ejemplo.es",
        role=RoleFactory(
            code="responsable-caja",
            name="Responsable",
            permissions=[
                "reservations.view_reservation",
                "billing.add_payment",
                "billing.view_billing",
                "billing.allow_overpayment",
            ],
        ),
        offices=[centro],
    )
