"""Pantallas de reservas: permisos, alta rapida y transiciones desde la UI."""

import pytest
from django.urls import reverse

from apps.reservations.models import Reservation, ReservationStatus, ReservationStatusChange

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserva(economico, centro, cliente):
    return ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
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


def test_el_listado_pide_permiso_de_lectura(client, reserva, centro):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    sin_nada = UserFactory(
        email="pelado@ejemplo.es",
        role=RoleFactory(code="pelado", name="Sin permisos"),
        offices=[centro],
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

    lejos = OfficeFactory(code="lejana", name="Oficina Lejana", pool=None)
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


def test_el_formulario_se_pinta(client, agente, economico, centro, tarifa):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:quick"))

    assert respuesta.status_code == 200
    contenido = respuesta.content.decode()
    assert "form-reserva" in contenido
    assert "panel-precio" in contenido


def test_el_panel_calcula_precio_y_disponibilidad(
    client, agente, economico, centro, cliente, coche, tarifa
):
    client.force_login(agente)

    respuesta = client.get(
        reverse("reservations:quick_preview"),
        {
            "customer": cliente.pk,
            "category": economico.pk,
            "pickup_office": centro.pk,
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
    client, agente, economico, centro, cliente, coche, tarifa
):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:quick"),
        {
            "customer": cliente.pk,
            "category": economico.pk,
            "pickup_office": centro.pk,
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
    client, agente, economico, centro, cliente, coche, tarifa
):
    """Nada escrito en base de datos y el motivo en pantalla."""
    from apps.reservations.services import create_quick_reservation

    create_quick_reservation(
        category=economico,
        pickup_office=centro,
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
            "pickup_office": centro.pk,
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "days": 3,
            "fuel_policy": "full_full",
            "cancellation_policy": "flexible",
        },
    )

    assert respuesta.status_code == 200
    assert Reservation.objects.count() == 1
    assert "No queda disponibilidad" in respuesta.content.decode()


def test_alta_rapida_de_cliente(client, agente, centro):
    from apps.accounts.tests.factories import RoleFactory, UserFactory
    from apps.customers.models import Customer

    usuario = UserFactory(
        email="alta@ejemplo.es",
        role=RoleFactory(
            code="alta-cli",
            name="Alta",
            permissions=["reservations.add_reservation", "customers.add_customer"],
        ),
        offices=[centro],
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


# ---------------------------------------------------------------------------
# Desplegable de clientes
# ---------------------------------------------------------------------------


def test_el_desplegable_se_abre_con_todos_los_clientes(client, agente, centro):
    """Al pinchar en el campo, sin escribir nada, salen los clientes."""
    from apps.customers.tests.factories import CustomerFactory

    for apellido in ("Zapata", "Alvarez", "Moreno"):
        CustomerFactory(first_name="Cliente", last_name=apellido)
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:customer_search"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert contenido.count('role="option"') == 3
    assert "Sin coincidencias" not in contenido


def test_el_desplegable_viene_ordenado_alfabeticamente(client, agente):
    from apps.customers.tests.factories import CustomerFactory

    for apellido in ("Zapata", "Alvarez", "Moreno"):
        CustomerFactory(first_name="Cliente", last_name=apellido)
    client.force_login(agente)

    contenido = client.get(reverse("reservations:customer_search")).content.decode()

    assert contenido.index("Alvarez") < contenido.index("Moreno") < contenido.index("Zapata")


def test_escribiendo_se_filtra(client, agente):
    from apps.customers.tests.factories import CustomerFactory

    CustomerFactory(first_name="Ana", last_name="Zapata")
    CustomerFactory(first_name="Luis", last_name="Moreno")
    client.force_login(agente)

    contenido = client.get(
        reverse("reservations:customer_search"), {"q": "zapata"}
    ).content.decode()

    assert "Zapata" in contenido
    assert "Moreno" not in contenido


def test_el_desplegable_pagina_al_bajar(client, agente):
    """Con muchos clientes no se trae la tabla entera: hay centinela de scroll."""
    from apps.customers.tests.factories import CustomerFactory
    from apps.reservations.views import CLIENTES_POR_PAGINA

    for indice in range(CLIENTES_POR_PAGINA + 5):
        CustomerFactory(first_name="Cliente", last_name=f"Apellido{indice:03d}")
    client.force_login(agente)

    primera = client.get(reverse("reservations:customer_search")).content.decode()

    assert primera.count('role="option"') == CLIENTES_POR_PAGINA
    assert "page=2" in primera, "falta el centinela que carga la pagina siguiente"

    segunda = client.get(reverse("reservations:customer_search"), {"page": 2}).content.decode()
    assert segunda.count('role="option"') == 5
    assert "page=3" not in segunda


def test_una_pagina_vacia_no_dice_sin_coincidencias(client, agente):
    """El mensaje solo tiene sentido en la primera pagina.

    Por defecto se ensena: un endpoint que no pagine (el del ui-kit, por
    ejemplo) no tiene que enterarse de que esto existe.
    """
    from apps.customers.tests.factories import CustomerFactory

    CustomerFactory(first_name="Ana", last_name="Zapata")
    client.force_login(agente)

    contenido = client.get(reverse("reservations:customer_search"), {"page": 9}).content.decode()

    assert "Sin coincidencias" not in contenido


# ---------------------------------------------------------------------------
# Alta de cliente desde la reserva
# ---------------------------------------------------------------------------


@pytest.fixture
def usuario_con_alta(db, centro):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    return UserFactory(
        email="alta@ejemplo.es",
        role=RoleFactory(
            code="alta-cliente",
            name="Alta",
            permissions=["reservations.add_reservation", "customers.add_customer"],
        ),
        offices=[centro],
    )


def _datos_cliente(**extra):
    datos = {
        "first_name": "Nueva",
        "last_name": "Clienta",
        "document_type": "dni",
        "document_number": "12345678Z",
        "phone": "600111222",
    }
    datos.update(extra)
    return datos


def test_el_error_del_alta_se_queda_en_el_modal(client, usuario_con_alta):
    """Antes se incrustaba dentro del formulario y salia un modal detras de otro."""
    client.force_login(usuario_con_alta)

    respuesta = client.post(reverse("reservations:quick_customer"), _datos_cliente(last_name=""))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 422
    # Vuelve el modal, no el bloque del formulario de la reserva.
    assert "form-cliente-rapido" in contenido
    assert 'id="bloque-cliente"' not in contenido
    assert 'hx-target="#modal-host"' in contenido


def test_al_crear_el_cliente_el_modal_se_cierra_y_el_campo_se_rellena(client, usuario_con_alta):
    from apps.customers.models import Customer

    client.force_login(usuario_con_alta)

    respuesta = client.post(reverse("reservations:quick_customer"), _datos_cliente())
    contenido = respuesta.content.decode()
    cliente = Customer.objects.get(document_number="12345678Z")

    assert respuesta.status_code == 200
    # Fuera de banda: el bloque va a su sitio y el modal se queda vacio.
    assert 'hx-swap-oob="true"' in contenido
    assert "form-cliente-rapido" not in contenido
    assert f'value="{cliente.pk}"' in contenido
    assert "Clienta" in contenido


def test_el_desplegable_escucha_el_swap_en_el_contenedor(client, agente):
    """El listener tiene que estar por encima de la lista, no al lado.

    `htmx:after-swap` se dispara en el destino del swap (la lista de opciones) y
    burbujea hacia arriba. Estuvo puesto en el input, que es su hermano, y por
    eso el desplegable no se abria nunca: el evento no pasaba por ahi.
    """
    client.force_login(agente)

    html = client.get(reverse("reservations:quick")).content.decode()

    contenedor = html.index('x-data="selectBuscador()"')
    listener = html.index('@htmx:after-swap="abrir()"')
    lista = html.index('id="customer-opciones"')
    input_buscador = html.index('id="customer-buscador"')

    # El listener esta en la apertura del contenedor, antes que el input y la
    # lista: es decir, en un ancestro de ambos.
    assert contenedor < listener < input_buscador < lista
    # Y ya no cuelga del input.
    assert "@htmx:after-swap" not in html[input_buscador:lista]


def test_el_desplegable_apunta_a_su_lista(client, agente):
    client.force_login(agente)

    html = client.get(reverse("reservations:quick")).content.decode()

    assert 'hx-target="#customer-opciones"' in html
    assert 'id="customer-opciones"' in html
    assert 'hx-trigger="focus, click, keyup changed delay:250ms"' in html
