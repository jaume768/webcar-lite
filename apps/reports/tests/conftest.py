"""Escenario de informes: una oficina, dos coches y alquileres de verdad."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory
from apps.reservations.tests.factories import en, fijar_referencia


@pytest.fixture(autouse=True)
def _series_por_defecto(db):
    """Las series de la migracion 0004, que un test transaccional se lleva."""
    from apps.billing.models import InvoiceKind, InvoiceSeries

    InvoiceSeries.objects.get_or_create(
        code="f",
        defaults={
            "name": "Facturas",
            "kind": InvoiceKind.ORDINARY,
            "number_format": "F{year}-{sequence:05d}",
            "is_default": True,
        },
    )


@pytest.fixture(autouse=True)
def _reloj_estable():
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
def economico(db):
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def ibiza(db, economico, centro):
    return VehicleFactory(
        plate="1111AAA", brand="Seat", model="Ibiza", category=economico, current_office=centro
    )


@pytest.fixture
def corsa(db, economico, centro):
    return VehicleFactory(
        plate="2222BBB", brand="Opel", model="Corsa", category=economico, current_office=centro
    )


@pytest.fixture
def tarifa(db, economico, centro, norte):
    return RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[economico],
        offices=[centro, norte],
        tiers=TRAMOS_ESTANDAR,
    )


@pytest.fixture
def analista(db, centro):
    """Responsable de oficina: ve los informes de su oficina."""
    return UserFactory(
        email="informes@ejemplo.es",
        role=RoleFactory(
            code="informes",
            name="Informes",
            permissions=[
                "reports.view_reports",
                "reservations.view_reservation",
                "reservations.cancel_reservation",
                "billing.view_billing",
                "fleet.view_vehicle",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def mostrador(db, centro):
    """Sin permiso de informes: el mostrador no ve la cuenta de resultados."""
    return UserFactory(
        email="mostrador-informes@ejemplo.es",
        role=RoleFactory(
            code="mostrador-informes",
            name="Mostrador",
            permissions=["reservations.view_reservation"],
        ),
        offices=[centro],
    )


@pytest.fixture
def alquilar(db, economico, tarifa):
    """Crea una reserva con coche asignado y la deja lista para el informe."""

    def _alquilar(*, vehiculo, oficina, desde_dias: float = 1, hasta_dias: float = 4, actor=None):
        from apps.availability.services import assign_vehicle
        from apps.reservations.services import create_quick_reservation

        reserva = create_quick_reservation(
            category=economico,
            pickup_office=oficina,
            customer=CustomerFactory(),
            pickup_at=en(desde_dias),
            return_at=en(hasta_dias),
            actor=actor,
        )
        assign_vehicle(reservation=reserva, vehicle=vehiculo, actor=actor)
        reserva.refresh_from_db()
        return reserva

    return _alquilar
