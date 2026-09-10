"""Pantallas de reservas: permisos, alta rapida y transiciones desde la UI."""

import pytest
from django.urls import reverse

from apps.reservations.models import Reservation, ReservationStatus, ReservationStatusChange

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserva(economico, palma, cliente):
    return ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=cliente,
        status=ReservationStatus.PENDING,
    )


# --- permisos ---------------------------------------------------------------

ENDPOINTS = [
    ("reservations:list", "get", False),
    ("reservations:quick", "get", False),
    ("reservations:quick", "post", False),
    ("reservations:quick_preview", "get", False),
    ("reservations:detail", "get", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_sesion_no_se_entra(client, reserva, vista, metodo, con_objeto):
    url = reverse(vista, args=[reserva.pk] if con_objeto else [])

    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code == 302
    assert reverse("accounts:login") in respuesta.headers["Location"]


def test_el_listado_pide_permiso_de_lectura(client, reserva, palma):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    sin_nada = UserFactory(
        email="pelado@ejemplo.es",
        role=RoleFactory(code="pelado", name="Sin permisos"),
        offices=[palma],
    )
    client.force_login(sin_nada)

    assert client.get(reverse("reservations:list")).status_code == 403


def test_el_alta_rapida_pide_permiso_de_alta(client, solo_lectura):
    client.force_login(solo_lectura)

    assert client.get(reverse("reservations:quick")).status_code == 403
    assert client.post(reverse("reservations:quick"), {}).status_code == 403


def test_una_transicion_sin_permiso_devuelve_403_y_no_deja_rastro(client, reserva, agente):
    """El agente no puede cancelar, ni forzando el POST al endpoint."""
    client.force_login(agente)
    url = reverse("reservations:transition", args=[reserva.pk, ReservationStatus.CANCELLED])

    respuesta = client.post(url, {"reason": "porque si"})

    assert respuesta.status_code == 403
    reserva.refresh_from_db()
    assert reserva.status == ReservationStatus.PENDING
    assert not ReservationStatusChange.objects.exists()


def test_no_se_ve_una_reserva_de_otra_oficina(client, economico, aeropuerto, cliente, agente):
    """Fuera del scope, la reserva ni siquiera existe."""
    from apps.offices.tests.factories import OfficeFactory

    lejos = OfficeFactory(code="vlc", name="Valencia", pool=None)
    ajena = ReservationFactory(
        category=economico, pickup_office=lejos, return_office=lejos, customer=cliente
    )
    client.force_login(agente)

    assert client.get(reverse("reservations:detail", args=[ajena.pk])).status_code == 404


# --- listado y ficha --------------------------------------------------------


def test_el_listado_se_ve(client, reserva, agente):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    lector = UserFactory(
        email="lector@ejemplo.es",
        role=RoleFactory(
            code="lector-res", name="Lector", permissions=["reservations.view_reservation"]
        ),
        offices=[reserva.pickup_office],
    )
    client.force_login(lector)

    respuesta = client.get(reverse("reservations:list"))

    assert respuesta.status_code == 200
    assert reserva.number in respuesta.content.decode()


def _url_cancelar(reserva):
    return reverse("reservations:transition", args=[reserva.pk, ReservationStatus.CANCELLED])


def _url_confirmar(reserva):
    return reverse("reservations:transition", args=[reserva.pk, ReservationStatus.CONFIRMED])


def test_la_ficha_muestra_las_transiciones_del_usuario(client, reserva, responsable):
    client.force_login(responsable)

    contenido = client.get(reverse("reservations:detail", args=[reserva.pk])).content.decode()

    assert _url_confirmar(reserva) in contenido
    assert _url_cancelar(reserva) in contenido


def test_la_ficha_no_ofrece_lo_que_el_usuario_no_puede(client, reserva, agente):
    """El agente confirma, pero el boton de cancelar no se le pinta.

    Se comprueba por la URL de la transicion y no por el texto: "Cancelar"
    aparece tambien en el dialogo de confirmacion que trae `base.html`.
    """
    client.force_login(agente)

    contenido = client.get(reverse("reservations:detail", args=[reserva.pk])).content.decode()

    assert _url_confirmar(reserva) in contenido
    assert _url_cancelar(reserva) not in contenido


# --- alta rapida ------------------------------------------------------------


def test_el_formulario_se_pinta(client, agente, economico, palma, tarifa):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:quick"))

    assert respuesta.status_code == 200
    contenido = respuesta.content.decode()
    assert "form-reserva" in contenido
    assert "panel-precio" in contenido


def test_el_panel_calcula_precio_y_disponibilidad(
    client, agente, economico, palma, cliente, coche, tarifa
):
    client.force_login(agente)

    respuesta = client.get(
        reverse("reservations:quick_preview"),
        {
            "customer": cliente.pk,
            "category": economico.pk,
            "pickup_office": palma.pk,
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "days": 3,
            "fuel_policy": "full_full",
            "cancellation_policy": "flexible",
        },
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "libres de 1" in contenido
    assert "163,35" in contenido  # 135 base + 21% IVA, con coma decimal


def test_el_panel_con_el_formulario_a_medias_no_se_queja(client, agente):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:quick_preview"), {})

    assert respuesta.status_code == 200
    assert "Elige categoria" in respuesta.content.decode()


def test_el_alta_crea_y_redirige_a_la_ficha(
    client, agente, economico, palma, cliente, coche, tarifa
):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:quick"),
        {
            "customer": cliente.pk,
            "category": economico.pk,
            "pickup_office": palma.pk,
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "days": 3,
            "fuel_policy": "full_full",
            "cancellation_policy": "flexible",
            "notes": "",
        },
    )

    reserva = Reservation.objects.get()
    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == reverse("reservations:detail", args=[reserva.pk])
    assert reserva.status == ReservationStatus.PENDING
    assert reserva.customer == cliente


def test_el_alta_sin_disponibilidad_se_queda_en_el_formulario(
    client, agente, economico, palma, cliente, coche, tarifa
):
    """Nada escrito en base de datos y el motivo en pantalla."""
    from apps.reservations.services import create_quick_reservation

    create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=cliente,
        pickup_at=en(1),
        return_at=en(4),
        actor=agente,
    )
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:quick"),
        {
            "customer": cliente.pk,
            "category": economico.pk,
            "pickup_office": palma.pk,
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "days": 3,
            "fuel_policy": "full_full",
            "cancellation_policy": "flexible",
        },
    )

    assert respuesta.status_code == 200
    assert Reservation.objects.count() == 1
    assert "No queda disponibilidad" in respuesta.content.decode()


def test_alta_rapida_de_cliente(client, agente, palma):
    from apps.accounts.tests.factories import RoleFactory, UserFactory
    from apps.customers.models import Customer

    usuario = UserFactory(
        email="alta@ejemplo.es",
        role=RoleFactory(
            code="alta-cli",
            name="Alta",
            permissions=["reservations.add_reservation", "customers.add_customer"],
        ),
        offices=[palma],
    )
    client.force_login(usuario)

    respuesta = client.post(
        reverse("reservations:quick_customer"),
        {
            "first_name": "Marta",
            "last_name": "Lopez",
            "document_type": "dni",
            "document_number": "12345678Z",
            "phone": "600111222",
        },
    )

    assert respuesta.status_code == 200
    cliente = Customer.objects.get(document_number="12345678Z")
    assert "Marta" in respuesta.content.decode()
    assert str(cliente.pk) in respuesta.content.decode()


def test_el_buscador_de_clientes_responde(client, agente, cliente):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:customer_search"), {"q": cliente.last_name})

    assert respuesta.status_code == 200
    assert cliente.last_name in respuesta.content.decode()
