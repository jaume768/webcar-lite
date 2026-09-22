"""API de reservas para la web del cliente."""

import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.auditlog.models import AuditLog
from apps.billing.models import OnlinePayment
from apps.booking_api.models import ApiReservation
from apps.customers.tests.factories import CustomerFactory
from apps.reservations.models import Reservation, ReservationStatus, ReservationStatusChange

from .conftest import en

pytestmark = pytest.mark.django_db


def _reservar(api, cuerpo, clave="pedido-1"):
    return api.post(
        "/api/v1/reservations/",
        cuerpo,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=clave,
    )


# ---------------------------------------------------------------------------
# Autenticacion y scope
# ---------------------------------------------------------------------------


def test_sin_clave_no_entra(client, web):
    respuesta = client.get("/api/v1/offices/")
    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["code"] == "unauthorized"


def test_una_clave_falsa_con_prefijo_bueno_no_entra(client, web):
    _cliente, clave = web
    falsa = clave[:-4] + "XXXX"
    respuesta = client.get("/api/v1/offices/", HTTP_AUTHORIZATION=f"Bearer {falsa}")
    assert respuesta.status_code == 401


def test_una_web_dada_de_baja_no_entra(api, web):
    web[0].deactivate()
    assert api.get("/api/v1/offices/").status_code == 401


def test_la_clave_no_se_guarda_en_claro(web):
    cliente, clave = web
    cliente.refresh_from_db()
    assert clave not in (cliente.key_hash, cliente.key_prefix)


def test_renovar_la_clave_invalida_la_anterior(client, web):
    from apps.booking_api.services import rotate_key

    cliente, vieja = web
    nueva = rotate_key(client=cliente)
    assert client.get("/api/v1/offices/", HTTP_AUTHORIZATION=f"Bearer {vieja}").status_code == 401
    assert client.get("/api/v1/offices/", HTTP_AUTHORIZATION=f"Bearer {nueva}").status_code == 200


def test_solo_ve_sus_oficinas(api, norte):
    codigos = [o["code"] for o in api.get("/api/v1/offices/").json()["results"]]
    assert codigos == ["aeropuerto", "centro"]


def test_no_puede_reservar_en_una_oficina_ajena(api, cuerpo, norte, coche, tarifa_web):
    cuerpo["pickup_office"] = "norte"
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "unknown_office"
    assert not Reservation.objects.exists()


def test_el_usuario_tecnico_no_puede_entrar_a_la_aplicacion(web):
    assert not web[0].user.has_usable_password()


def test_limite_de_peticiones(api, settings):
    settings.BOOKING_API_RATE_LIMIT_PER_MINUTE = 2
    from django.core.cache import cache

    cache.clear()
    codigos = [api.get("/api/v1/offices/").status_code for _ in range(3)]
    assert codigos == [200, 200, 429]


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------


def _buscar(api, **params):
    params.setdefault("pickup_office", "centro")
    params.setdefault("pickup_at", en(3).isoformat())
    params.setdefault("return_at", en(6).isoformat())
    return api.get("/api/v1/availability/", params)


def test_disponibilidad_con_precio_web(api, coche, tarifa_web):
    respuesta = _buscar(api)
    assert respuesta.status_code == 200
    [oferta] = respuesta.json()["results"]
    assert oferta["category"]["code"] == "eco"
    assert oferta["available_units"] == 1
    # 3 dias a 45 EUR/dia, IVA aparte.
    assert oferta["price"]["base_amount"] == "135.00"
    assert oferta["price"]["rental_days"] == 3


def test_sin_tarifa_web_la_categoria_no_se_ofrece(api, coche):
    assert _buscar(api).json()["results"] == []


def test_sin_coches_no_se_ofrece(api, economico, tarifa_web):
    assert _buscar(api).json()["results"] == []


def test_fechas_invalidas(api, coche, tarifa_web):
    respuesta = _buscar(api, pickup_at=en(5).isoformat(), return_at=en(3).isoformat())
    assert respuesta.status_code == 400
    assert "return_at" in respuesta.json()["error"]["fields"]


def test_no_se_reserva_con_menos_antelacion_de_la_minima(api, coche, tarifa_web, settings):
    settings.BOOKING_API_MIN_LEAD_MINUTES = 60 * 24 * 10
    respuesta = _buscar(api)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "too_soon"


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


def test_alta_de_reserva_web(api, web, cuerpo, coche, tarifa_web):
    respuesta = _reservar(api, cuerpo)

    assert respuesta.status_code == 201
    datos = respuesta.json()
    reserva = Reservation.objects.get(number=datos["number"])
    assert reserva.status == ReservationStatus.PENDING
    assert reserva.channel == "web"
    assert reserva.return_office.code == "aeropuerto"
    assert reserva.customer.document_number == "12345678Z"
    assert reserva.customer.language == "en"
    assert reserva.customer.office.code == "centro"
    assert datos["price"]["total"] == str(reserva.total)
    assert Decimal(datos["pending_amount"]) == reserva.total
    # Queda firmada por el usuario tecnico de la web, en el historico y en auditoria.
    cambio = ReservationStatusChange.objects.get(reservation=reserva)
    assert cambio.changed_by == web[0].user
    assert "Web principal" in cambio.reason
    assert AuditLog.objects.filter(reservation_id=reserva.pk, actor=web[0].user).exists()
    assert ApiReservation.objects.get(reservation=reserva).external_ref == "WEB-1"


def test_repetir_la_peticion_no_duplica(api, cuerpo, coche, tarifa_web):
    primera = _reservar(api, cuerpo)
    segunda = _reservar(api, cuerpo)

    assert primera.status_code == 201
    assert segunda.status_code == 200
    assert segunda.json()["number"] == primera.json()["number"]
    assert Reservation.objects.count() == 1


def test_misma_clave_con_otra_peticion_es_un_conflicto(api, cuerpo, coche, tarifa_web):
    _reservar(api, cuerpo)
    cuerpo["return_at"] = en(7).isoformat()
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["code"] == "idempotency_conflict"


def test_sin_clave_de_idempotencia_no_se_reserva(api, cuerpo, coche, tarifa_web):
    respuesta = api.post("/api/v1/reservations/", cuerpo, content_type="application/json")
    assert respuesta.status_code == 400
    assert not Reservation.objects.exists()


def test_sin_hueco_no_se_reserva(api, cuerpo, coche, tarifa_web):
    assert _reservar(api, cuerpo, "a").status_code == 201
    cuerpo["customer"]["document_number"] = "87654321X"
    respuesta = _reservar(api, cuerpo, "b")
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["code"] == "not_available"
    assert Reservation.objects.count() == 1


def test_documento_mal_escrito(api, cuerpo, coche, tarifa_web):
    cuerpo["customer"]["document_number"] = "12345678A"
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 400
    assert "customer.document_number" in respuesta.json()["error"]["fields"]


def test_faltan_datos_del_cliente(api, cuerpo, coche, tarifa_web):
    del cuerpo["customer"]["phone"]
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 400
    assert "customer.phone" in respuesta.json()["error"]["fields"]


def test_cliente_que_vuelve_se_reutiliza_sin_pisar_sus_datos(api, cuerpo, coche, tarifa_web):
    existente = CustomerFactory(
        document_type="dni", document_number="12345678Z", email="", phone="611000000"
    )

    respuesta = _reservar(api, cuerpo)

    assert respuesta.status_code == 201
    existente.refresh_from_db()
    assert Reservation.objects.get().customer == existente
    assert existente.phone == "611000000"  # lo que habia se queda
    assert existente.email == "lucia@example.com"  # lo que faltaba se completa


def test_cliente_marcado_no_reserva_y_no_se_dice_por_que(api, cuerpo, coche, tarifa_web):
    CustomerFactory(
        document_type="dni",
        document_number="12345678Z",
        is_blacklisted=True,
        blacklist_reason="Impago",
    )
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "not_bookable"
    assert "Impago" not in respuesta.content.decode()
    assert not Reservation.objects.exists()


def test_extras(api, cuerpo, coche, tarifa_web):
    from apps.reservations.tests.factories import ExtraFactory

    ExtraFactory(code="silla", name="Silla", price=Decimal("10.00"), max_quantity=2)
    cuerpo["extras"] = [{"code": "silla", "quantity": 2}]

    respuesta = _reservar(api, cuerpo)

    assert respuesta.status_code == 201
    assert respuesta.json()["extras"][0]["quantity"] == 2


def test_extra_desconocido(api, cuerpo, coche, tarifa_web):
    cuerpo["extras"] = [{"code": "no-existe"}]
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "unknown_extra"


def test_json_roto(api, coche, tarifa_web):
    respuesta = api.post(
        "/api/v1/reservations/", "{no", content_type="application/json", HTTP_IDEMPOTENCY_KEY="x"
    )
    assert respuesta.status_code == 400


# ---------------------------------------------------------------------------
# Despues del alta
# ---------------------------------------------------------------------------


def test_solo_lee_sus_propias_reservas(api, cuerpo, coche, tarifa_web, roles, centro):
    from apps.booking_api.services import create_client

    numero = _reservar(api, cuerpo).json()["number"]
    assert api.get(f"/api/v1/reservations/{numero}/").status_code == 200

    _otra, clave_otra = create_client(name="Otra web", offices=[centro])
    respuesta = api.get(
        f"/api/v1/reservations/{numero}/", HTTP_AUTHORIZATION=f"Bearer {clave_otra}"
    )
    assert respuesta.status_code == 404


def test_cancelar_por_la_maquina_de_estados(api, cuerpo, coche, tarifa_web):
    numero = _reservar(api, cuerpo).json()["number"]

    respuesta = api.post(f"/api/v1/reservations/{numero}/cancel/")

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == ReservationStatus.CANCELLED
    reserva = Reservation.objects.get(number=numero)
    assert reserva.status_changes.filter(to_status=ReservationStatus.CANCELLED).exists()
    # Y el hueco vuelve a estar libre.
    assert _buscar(api).json()["results"]


def test_no_se_cancela_dos_veces(api, cuerpo, coche, tarifa_web):
    numero = _reservar(api, cuerpo).json()["number"]
    api.post(f"/api/v1/reservations/{numero}/cancel/")
    respuesta = api.post(f"/api/v1/reservations/{numero}/cancel/")
    assert respuesta.status_code == 409


# ---------------------------------------------------------------------------
# Pago: Stripe o Redsys, a eleccion de la web
# ---------------------------------------------------------------------------


@pytest.fixture
def pasarelas(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    settings.REDSYS_MERCHANT_CODE = "999008881"
    settings.REDSYS_SECRET_KEY = "sq7HjrUOBfKmC576ILgskD5srU870gJ7"


def test_pasarelas_disponibles(api, pasarelas):
    codigos = [p["code"] for p in api.get("/api/v1/payment-providers/").json()["results"]]
    assert codigos == ["stripe", "redsys"]


@pytest.mark.parametrize("pasarela", ["stripe", "redsys"])
def test_alta_con_enlace_de_pago(api, cuerpo, coche, tarifa_web, pasarelas, pasarela):
    cuerpo["payment"] = {"provider": pasarela}

    respuesta = _reservar(api, cuerpo)

    assert respuesta.status_code == 201
    [pago] = respuesta.json()["payments"]
    online = OnlinePayment.objects.get()
    assert pago["provider"] == pasarela
    assert pago["amount"] == str(online.reservation.total)
    assert pago["url"].endswith(f"/pago/{online.token}/")


def test_anticipo_por_enlace(api, cuerpo, coche, tarifa_web, pasarelas):
    numero = _reservar(api, cuerpo).json()["number"]

    respuesta = api.post(
        f"/api/v1/reservations/{numero}/payment-link/",
        {"provider": "redsys", "purpose": "advance", "amount": "50.00"},
        content_type="application/json",
    )

    assert respuesta.status_code == 201
    assert respuesta.json()["amount"] == "50.00"
    assert respuesta.json()["purpose"] == "advance"


def test_pasarela_sin_configurar_no_crea_la_reserva(api, cuerpo, coche, tarifa_web):
    cuerpo["payment"] = {"provider": "stripe"}
    respuesta = _reservar(api, cuerpo)
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["code"] == "payment_provider_disabled"
    assert not Reservation.objects.exists()


# ---------------------------------------------------------------------------
# Concurrencia
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_dos_webs_a_la_vez_no_venden_el_ultimo_coche_dos_veces(
    web, cuerpo, coche, tarifa_web, roles, centro, aeropuerto
):
    from apps.booking_api.services import create_client

    _otra, clave_otra = create_client(name="Otra web", offices=[centro, aeropuerto])
    claves = [web[1], clave_otra]
    documentos = ["12345678Z", "87654321X"]
    resultados = []
    barrera = threading.Barrier(2)

    def reservar(indice):
        from django.test import Client

        http = Client(HTTP_AUTHORIZATION=f"Bearer {claves[indice]}")
        datos = {
            **cuerpo,
            "customer": {**cuerpo["customer"], "document_number": documentos[indice]},
        }
        barrera.wait()
        try:
            respuesta = http.post(
                "/api/v1/reservations/",
                datos,
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=f"c-{indice}",
            )
            resultados.append(respuesta.status_code)
        finally:
            connection.close()

    hilos = [threading.Thread(target=reservar, args=(i,)) for i in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert sorted(resultados) == [201, 409]
    assert Reservation.objects.count() == 1


def test_el_rol_de_la_api_no_se_ofrece_a_personas(roles):
    from apps.accounts.forms import UserForm

    codigos = set(UserForm().fields["role"].queryset.values_list("code", flat=True))
    assert "api_web" not in codigos
    assert "mostrador" in codigos
