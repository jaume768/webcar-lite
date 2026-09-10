"""Escenario para emitir contratos: empresa, condiciones y una reserva real."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory
from apps.reservations.tests.factories import en, fijar_referencia
from apps.settings_app.models import CompanySettings, TermsVersion

CLAUSULAS = """El arrendatario se compromete a devolver el vehiculo en la fecha pactada.
El vehiculo no podra ser conducido por personas distintas de las autorizadas.
El combustible se factura segun la politica indicada en este contrato.
Los danos no declarados en la entrega se consideran causados durante el alquiler."""


@pytest.fixture(autouse=True)
def _reloj_estable():
    fijar_referencia(timezone.now())
    yield
    fijar_referencia(None)


@pytest.fixture
def empresa(db):
    datos = CompanySettings.load()
    datos.legal_name = "Baleares Rent a Car, S.L."
    datos.trade_name = "Baleares Rent"
    datos.tax_id = "B07123456"
    datos.address = "Carrer de la Mar, 12"
    datos.city = "Palma"
    datos.province = "Illes Balears"
    datos.postal_code = "07001"
    datos.phone = "971 000 111"
    datos.email = "reservas@ejemplo.es"
    datos.save()
    return datos


@pytest.fixture
def condiciones(db):
    version = TermsVersion.objects.create(title="Condiciones generales", body=CLAUSULAS)
    return version.publish()


@pytest.fixture
def palma(db):
    return OfficeFactory(code="palma", name="Palma Centro", pool=OfficePoolFactory(code="bal"))


@pytest.fixture
def alcudia(db, palma):
    return OfficeFactory(code="alcudia", name="Alcudia Puerto", pool=palma.pool)


@pytest.fixture
def empleado(db, palma):
    return UserFactory(
        email="mostrador@ejemplo.es",
        role=RoleFactory(
            code="mostrador-doc",
            name="Mostrador",
            permissions=[
                "reservations.view_reservation",
                "reservations.add_reservation",
                "reservations.change_reservation",
            ],
        ),
        offices=[palma],
    )


@pytest.fixture
def ajeno(db, alcudia):
    """Mismo permiso, otra oficina: no tiene por que ver esta reserva."""
    return UserFactory(
        email="alcudia@ejemplo.es",
        role=RoleFactory(
            code="mostrador-alcudia",
            name="Mostrador Alcudia",
            permissions=["reservations.view_reservation", "reservations.change_reservation"],
        ),
        offices=[alcudia],
    )


@pytest.fixture
def reserva(db, palma, empleado):
    from apps.availability.services import assign_vehicle
    from apps.reservations.services import create_quick_reservation

    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    coche = VehicleFactory(
        plate="1234ABC", category=categoria, current_office=palma, brand="Seat", model="Ibiza"
    )
    RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[categoria],
        offices=[palma],
        tiers=TRAMOS_ESTANDAR,
    )
    reserva = create_quick_reservation(
        category=categoria,
        pickup_office=palma,
        customer=CustomerFactory(
            first_name="Ana", last_name="Garcia Lopez", licence_number="B-123456"
        ),
        pickup_at=en(1),
        return_at=en(4),
        actor=empleado,
    )
    assign_vehicle(reservation=reserva, vehicle=coche, actor=empleado)
    reserva.refresh_from_db()
    return reserva


@pytest.fixture
def emitir(django_capture_on_commit_callbacks):
    """Emite un contrato ejecutando la tarea encolada.

    `request_contract` encola con `transaction.on_commit`, que dentro de un test
    no llega a dispararse porque la transaccion se deshace al terminar. Esta
    fixture ejecuta esas llamadas, que es lo que pasa en produccion al confirmar.
    """
    from apps.contracts.services import request_contract

    def _emitir(reservation, actor=None):
        with django_capture_on_commit_callbacks(execute=True):
            contrato = request_contract(reservation=reservation, actor=actor)
        contrato.refresh_from_db()
        return contrato

    return _emitir
