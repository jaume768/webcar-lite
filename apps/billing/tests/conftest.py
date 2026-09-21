"""Escenario de cobros y facturas: una reserva de 163,35 EUR y usuarios por rol."""

import pytest

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory
from apps.reservations.models import Reservation, ReservationStatus
from apps.reservations.tests.factories import en, fijar_referencia


@pytest.fixture(autouse=True)
def _series_por_defecto(db):
    """Las series que crea la migracion 0004.

    Un test con transacciones reales vacia la base de datos al terminar, datos
    de migracion incluidos: sin esto, los tests que vienen detras se quedarian
    sin serie donde numerar.
    """
    from apps.billing.models import InvoiceKind, InvoiceSeries

    for codigo, nombre, tipo, formato in (
        ("f", "Facturas", InvoiceKind.ORDINARY, "F{year}-{sequence:05d}"),
        ("fr", "Rectificativas", InvoiceKind.RECTIFYING, "FR{year}-{sequence:05d}"),
    ):
        InvoiceSeries.objects.get_or_create(
            code=codigo,
            defaults={"name": nombre, "kind": tipo, "number_format": formato, "is_default": True},
        )


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


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------


def finalizar(reserva) -> Reservation:
    """Deja la reserva finalizada. Es preparar el escenario, no probar la maquina de estados."""
    Reservation.objects.filter(pk=reserva.pk).update(status=ReservationStatus.FINISHED)
    reserva.refresh_from_db()
    return reserva


@pytest.fixture(autouse=True)
def empresa(db):
    from apps.settings_app.models import CompanySettings

    datos = CompanySettings.load()
    datos.legal_name = "Alquileres Centro, S.L."
    datos.tax_id = "B07456123"
    datos.address = "Calle Mayor, 1"
    datos.city = "Palma"
    datos.postal_code = "07001"
    datos.save()
    return datos


@pytest.fixture
def facturador(db, centro):
    return UserFactory(
        email="facturador@ejemplo.es",
        role=RoleFactory(
            code="facturador",
            name="Facturador",
            permissions=[
                "reservations.view_reservation",
                "billing.view_billing",
                "billing.add_invoice",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def administrador(db, centro):
    return UserFactory(
        email="admin-facturas@ejemplo.es",
        role=RoleFactory(
            code="admin-facturas",
            name="Administracion",
            permissions=[
                "reservations.view_reservation",
                "billing.view_billing",
                "billing.add_invoice",
                "billing.rectify_invoice",
                "billing.view_invoiceseries",
                "billing.add_invoiceseries",
                "billing.change_invoiceseries",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def finalizada(reserva):
    return finalizar(reserva)


@pytest.fixture
def otra_finalizada(reserva, centro):
    """Segunda reserva de la misma oficina, en otras fechas."""
    from apps.customers.tests.factories import CustomerFactory
    from apps.reservations.services import create_quick_reservation
    from apps.reservations.tests.factories import en

    otra = create_quick_reservation(
        category=reserva.category,
        pickup_office=centro,
        customer=CustomerFactory(),
        pickup_at=en(10),
        return_at=en(13),
    )
    return finalizar(otra)
