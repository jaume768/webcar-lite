"""Escenario de mostrador: una reserva confirmada con coche asignado."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory
from apps.reservations.tests.factories import en, fijar_referencia


@pytest.fixture(autouse=True)
def _reloj_estable():
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
    return VehicleFactory(
        plate="1234ABC", category=economico, current_office=centro, mileage=10000, tank_liters=50
    )


@pytest.fixture
def tarifa(db, economico, centro):
    return RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[economico],
        offices=[centro],
        tiers=TRAMOS_ESTANDAR,
    )


@pytest.fixture
def empleado(db, centro):
    """Mostrador: entrega, devuelve y cobra."""
    return UserFactory(
        email="mostrador@ejemplo.es",
        role=RoleFactory(
            code="mostrador-ops",
            name="Mostrador",
            permissions=[
                "reservations.view_reservation",
                "reservations.add_reservation",
                "reservations.change_reservation",
                "billing.add_payment",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def reserva(db, economico, centro, coche, tarifa, empleado):
    """Confirmada, con coche asignado y lista para entregar."""
    from apps.availability.services import assign_vehicle
    from apps.reservations.models import ReservationStatus
    from apps.reservations.services import create_quick_reservation
    from apps.reservations.state_machine import transition

    reserva = create_quick_reservation(
        category=economico,
        pickup_office=centro,
        customer=CustomerFactory(),
        pickup_at=en(1),
        return_at=en(4),
        actor=empleado,
    )
    assign_vehicle(reservation=reserva, vehicle=coche, actor=empleado)
    reserva.refresh_from_db()
    return transition(reserva, ReservationStatus.CONFIRMED, empleado)
