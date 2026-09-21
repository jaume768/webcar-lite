"""Una reserva con factura en vigor no se toca. Se corrige con rectificativa."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.billing.services import issue_invoice, rectify_invoice
from apps.contracts.services import request_contract
from apps.fleet.models import Vehicle
from apps.reservations.invoiced import InvoicedReservationError
from apps.reservations.models import ReservationStatus
from apps.reservations.services import add_driver, recalculate_price, release_vehicle
from apps.reservations.state_machine import available_transitions, transition

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


@pytest.fixture
def mostrador(db, centro):
    """Puede editar reservas y ver la ficha entera."""
    return UserFactory(
        email="mostrador-fact@ejemplo.es",
        role=RoleFactory(
            code="mostrador-fact",
            name="Mostrador",
            permissions=[
                "reservations.view_reservation",
                "reservations.change_reservation",
                "reservations.change_reservation_price",
                "reservations.cancel_reservation",
                "billing.view_billing",
                "billing.add_payment",
            ],
        ),
        offices=[centro],
    )


@pytest.fixture
def facturada(finalizada, facturador):
    finalizada.vehicle = Vehicle.objects.get(plate="1234ABC")
    finalizada.save(update_fields=["vehicle"])
    factura = issue_invoice(reservation=finalizada, actor=facturador)
    finalizada.refresh_from_db()
    finalizada.factura = factura
    return finalizada


# ---------------------------------------------------------------------------
# Servicios: la barrera de verdad
# ---------------------------------------------------------------------------


def test_no_se_recalcula_el_precio(facturada, mostrador):
    with pytest.raises(InvoicedReservationError) as fallo:
        recalculate_price(reservation=facturada, actor=mostrador)
    assert facturada.factura.number in str(fallo.value)


def test_no_se_suelta_el_coche(facturada, mostrador):
    with pytest.raises(InvoicedReservationError):
        release_vehicle(reservation=facturada, actor=mostrador)
    facturada.refresh_from_db()
    assert facturada.vehicle is not None


def test_no_se_autorizan_conductores(facturada, mostrador):
    from apps.reservations.models import ReservationDriver

    conductor = ReservationDriver(
        first_name="Luis", last_name="Pons", licence_number="B-1", licence_country="ES"
    )
    with pytest.raises(InvoicedReservationError):
        add_driver(reservation=facturada, driver=conductor, actor=mostrador)
    assert not facturada.drivers.exists()


def test_no_se_genera_otro_contrato(facturada, mostrador):
    with pytest.raises(InvoicedReservationError):
        request_contract(reservation=facturada, actor=mostrador)


def test_no_cambia_de_estado(facturada, mostrador):
    Reservation = type(facturada)
    Reservation.objects.filter(pk=facturada.pk).update(status=ReservationStatus.CONFIRMED)
    facturada.refresh_from_db()

    assert available_transitions(facturada, mostrador) == []
    with pytest.raises(InvoicedReservationError):
        transition(facturada, ReservationStatus.CANCELLED, mostrador, reason="prueba")


def test_no_se_anotan_danos(client, facturada, mostrador):
    client.force_login(mostrador)
    respuesta = client.post(
        reverse("operations:damage_create", args=[facturada.pk]),
        {"zone": "roof", "damage_type": "scratch", "severity": "minor"},
        headers=HTMX,
    )
    assert respuesta.status_code == 422
    assert not facturada.damages.exists()


def test_la_rectificativa_la_vuelve_a_abrir(facturada, mostrador, administrador):
    rectify_invoice(invoice=facturada.factura, reason="Fechas mal", actor=administrador)

    release_vehicle(reservation=facturada, actor=mostrador)

    facturada.refresh_from_db()
    assert facturada.vehicle is None


def test_cobrar_lo_pendiente_si_se_puede(facturada, cajero):
    """El dinero no es la reserva: se cobra aunque este facturada."""
    from decimal import Decimal

    from apps.billing.models import PaymentMethod, PaymentType
    from apps.billing.services import register_payment

    cobro = register_payment(
        reservation=facturada,
        amount=Decimal("10.00"),
        method=PaymentMethod.CASH,
        payment_type=PaymentType.PAYMENT,
        office=facturada.pickup_office,
        actor=cajero,
    )
    assert cobro.pk


# ---------------------------------------------------------------------------
# Pantalla: sin botones que no llevan a ningun sitio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pestana", "acciones"),
    [
        ("resumen", ["reservations:change_dates", "reservations:change_category"]),
        ("cliente", ["reservations:driver_create"]),
        ("vehiculo", ["reservations:assign_vehicle", "reservations:release_vehicle"]),
        ("extras", ["reservations:extra_add"]),
        ("precio", ["reservations:recalculate_price", "reservations:manual_price"]),
        ("checkout", ["operations:damage_create"]),
        ("documentos", ["contracts:create"]),
    ],
)
def test_la_ficha_facturada_no_ofrece_editar(client, facturada, mostrador, pestana, acciones):
    client.force_login(mostrador)

    contenido = client.get(
        reverse("reservations:tab", args=[facturada.pk, pestana])
    ).content.decode()

    for accion in acciones:
        assert reverse(accion, args=[facturada.pk]) not in contenido, accion


def test_la_ficha_avisa_de_que_esta_facturada(client, facturada, mostrador):
    client.force_login(mostrador)

    contenido = client.get(reverse("reservations:detail", args=[facturada.pk])).content.decode()

    assert f"Reserva facturada en {facturada.factura.number}" in contenido
    assert "rectificativa" in contenido


def test_sin_factura_los_botones_siguen_ahi(client, reserva, mostrador):
    client.force_login(mostrador)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "resumen"])
    ).content.decode()

    assert reverse("reservations:change_dates", args=[reserva.pk]) in contenido
