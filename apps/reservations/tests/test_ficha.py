"""La ficha de reserva: cabecera, pestanas y cambios desde ella."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.availability.services import VehicleNotAvailableError, reserve_capacity
from apps.reservations.models import Reservation, ReservationDriver, ReservationStatus
from apps.reservations.services import (
    InvoicedReservationError,
    apply_change,
    preview_change,
    release_vehicle,
)

from .factories import en

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserva(economico, palma, cliente, coche, tarifa, agente):
    from apps.reservations.services import create_quick_reservation

    return create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=cliente,
        pickup_at=en(1),
        return_at=en(4),
        actor=agente,
    )


# ---------------------------------------------------------------------------
# Cabecera y pestanas
# ---------------------------------------------------------------------------


def test_la_ficha_trae_cabecera_y_pestanas(client, reserva, agente):
    client.force_login(agente)

    contenido = client.get(reverse("reservations:detail", args=[reserva.pk])).content.decode()

    assert 'id="cabecera-reserva"' in contenido
    assert 'id="panel-pestana"' in contenido
    assert reserva.number in contenido
    # La cabecera lleva cobrado y pendiente aunque facturacion no exista aun.
    assert "Cobrado / pendiente" in contenido


def test_la_cabecera_escucha_los_cambios(client, reserva, agente):
    """Sin esto no se repintaria sola tras un cambio."""
    client.force_login(agente)

    contenido = client.get(reverse("reservations:detail", args=[reserva.pk])).content.decode()

    assert 'hx-trigger="reserva:actualizada from:body"' in contenido
    assert reverse("reservations:header", args=[reserva.pk]) in contenido


def test_la_cabecera_se_sirve_suelta(client, reserva, agente):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:header", args=[reserva.pk]))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in contenido
    assert 'id="cabecera-reserva"' in contenido


@pytest.mark.parametrize("pestana", ["resumen", "cliente", "vehiculo", "historial"])
def test_las_pestanas_implementadas_responden_sueltas(client, reserva, agente, pestana):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:tab", args=[reserva.pk, pestana]))

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in respuesta.content.decode()


@pytest.mark.parametrize("pestana", ["extras", "precio", "cobros", "checkin", "checkout"])
def test_las_pestanas_que_faltan_lo_dicen(client, reserva, agente, pestana):
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:tab", args=[reserva.pk, pestana]))

    assert respuesta.status_code == 200
    assert "todavia no existe" in respuesta.content.decode()


def test_una_ficha_de_otra_oficina_no_existe(client, economico, cliente, agente):
    from apps.offices.tests.factories import OfficeFactory

    from .factories import ReservationFactory

    lejos = OfficeFactory(code="vlc", name="Valencia", pool=None)
    ajena = ReservationFactory(
        category=economico, pickup_office=lejos, return_office=lejos, customer=cliente
    )
    client.force_login(agente)

    for url in (
        reverse("reservations:tab", args=[ajena.pk, "resumen"]),
        reverse("reservations:header", args=[ajena.pk]),
    ):
        assert client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Cambio de fechas
# ---------------------------------------------------------------------------


def test_el_preview_calcula_la_diferencia_sin_tocar_nada(reserva, agente):
    """De 3 a 5 dias: el tramo baja a 40 EUR/dia pero se pagan dos dias mas."""
    antes = reserva.total

    preview = preview_change(
        reservation=reserva, return_at=reserva.return_at + timedelta(days=2), actor=agente
    )

    reserva.refresh_from_db()
    assert reserva.total == antes, "el preview no escribe"
    assert preview.new_total != antes
    assert preview.difference == preview.new_total - antes
    assert preview.availability.available


def test_aplicar_el_cambio_mueve_fechas_y_precio(reserva, agente):
    nueva_devolucion = reserva.return_at + timedelta(days=2)

    cambiada = apply_change(reservation=reserva, return_at=nueva_devolucion, actor=agente)

    assert cambiada.return_at == nueva_devolucion
    assert cambiada.price_breakdown["rental_days"] == 5
    # 5 dias al tramo 4-7 (40 EUR/dia).
    assert cambiada.base_amount == Decimal("200.00")


def test_cambiar_fechas_no_cuenta_la_reserva_a_si_misma(reserva, agente):
    """Con un solo coche, alargarla tiene que poder: se excluye del recuento."""
    ampliada = apply_change(
        reservation=reserva, return_at=reserva.return_at + timedelta(days=1), actor=agente
    )

    assert ampliada.pk == reserva.pk


def test_el_conflicto_de_vehiculo_nombra_la_reserva_que_estorba(
    reserva, economico, palma, cliente, coche, tarifa, agente
):
    """Criterio de aceptacion: el error dice cual es la reserva en conflicto."""
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    siguiente = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(6),
        end=en(8),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )

    with pytest.raises(VehicleNotAvailableError) as fallo:
        apply_change(reservation=reserva, return_at=en(7), actor=agente)

    mensaje = str(fallo.value)
    assert siguiente.number in mensaje
    assert coche.plate in mensaje

    reserva.refresh_from_db()
    assert reserva.return_at == en(4), "no se ha guardado nada"


def test_liberando_el_vehiculo_el_cambio_sale(reserva, economico, palma, coche, tarifa, agente):
    """La salida que ofrece el mostrador: soltar el coche y seguir."""
    from apps.availability.services import assign_vehicle
    from apps.fleet.tests.factories import VehicleFactory

    VehicleFactory(plate="2222BBB", category=economico, current_office=palma)
    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(6),
        end=en(8),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )

    cambiada = apply_change(
        reservation=reserva, return_at=en(7), release_vehicle=True, actor=agente
    )

    assert cambiada.vehicle is None
    assert cambiada.needs_reassignment
    assert cambiada.return_at == en(7)


def test_el_preview_avisa_del_conflicto_antes_de_confirmar(
    reserva, economico, palma, coche, agente
):
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    otra = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(6),
        end=en(8),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )

    preview = preview_change(reservation=reserva, return_at=en(7), actor=agente)

    assert otra.number in preview.vehicle_conflict


# ---------------------------------------------------------------------------
# Cambio de categoria
# ---------------------------------------------------------------------------


@pytest.fixture
def premium(db, palma, tarifa):
    from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
    from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory

    categoria = VehicleCategoryFactory(code="premium", name="Premium")
    VehicleFactory(plate="7777PRE", category=categoria, current_office=palma)
    RateFactory(
        code="premium",
        name="Premium",
        categories=[categoria],
        offices=[palma],
        tiers=TRAMOS_ESTANDAR,
    )
    return categoria


def test_cambiar_de_categoria_suelta_el_coche_que_ya_no_encaja(reserva, premium, coche, agente):
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)

    cambiada = apply_change(reservation=reserva, category=premium, actor=agente)

    assert cambiada.category == premium
    assert cambiada.vehicle is None
    assert cambiada.needs_reassignment


def test_el_preview_avisa_de_que_soltara_el_coche(reserva, premium, coche, agente):
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)

    preview = preview_change(reservation=reserva, category=premium, actor=agente)

    assert preview.releases_vehicle
    assert any("no es de la categoria" in aviso for aviso in preview.warnings)


# ---------------------------------------------------------------------------
# Factura emitida
# ---------------------------------------------------------------------------


def test_una_reserva_facturada_se_bloquea(reserva, agente, monkeypatch):
    """Facturacion todavia no existe: se simula que ya emitio."""
    monkeypatch.setattr("apps.reservations.services.has_issued_invoice", lambda r: True)

    with pytest.raises(InvoicedReservationError) as fallo:
        apply_change(reservation=reserva, return_at=en(6), actor=agente)

    assert reserva.number in str(fallo.value)
    reserva.refresh_from_db()
    assert reserva.return_at == en(4)


def test_con_permiso_si_se_puede_tocar_lo_facturado(reserva, monkeypatch, palma):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    monkeypatch.setattr("apps.reservations.services.has_issued_invoice", lambda r: True)
    administracion = UserFactory(
        email="admin@ejemplo.es",
        role=RoleFactory(
            code="admin-fact",
            name="Administracion",
            permissions=[
                "reservations.change_reservation",
                "reservations.change_invoiced_reservation",
            ],
        ),
        offices=[palma],
    )

    cambiada = apply_change(reservation=reserva, return_at=en(6), actor=administracion)

    assert cambiada.return_at == en(6)


def test_el_preview_avisa_de_la_factura_en_vez_de_reventar(reserva, agente, monkeypatch):
    monkeypatch.setattr("apps.reservations.services.has_issued_invoice", lambda r: True)

    preview = preview_change(reservation=reserva, return_at=en(6), actor=agente)

    assert "facturada" in preview.blocked_reason
    assert not preview.can_confirm


# ---------------------------------------------------------------------------
# Vehiculo
# ---------------------------------------------------------------------------


def test_las_opciones_dicen_por_que_se_descarta_cada_coche(
    reserva, economico, palma, coche, agente
):
    from apps.availability.services import vehicle_options
    from apps.fleet.tests.factories import VehicleBlockFactory, VehicleFactory

    en_taller = VehicleFactory(plate="3333TAL", category=economico, current_office=palma)
    VehicleBlockFactory(vehicle=en_taller, start_at=en(0.5), end_at=en(5))
    ocupado = VehicleFactory(plate="4444OCU", category=economico, current_office=palma)
    otra = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(4),
        vehicle=ocupado,
        status=ReservationStatus.CONFIRMED,
    )

    opciones = {
        opcion.plate: opcion
        for opcion in vehicle_options(
            economico, palma, reserva.pickup_at, reserva.return_at, exclude_reservation=reserva
        )
    }

    assert opciones["1234ABC"].available
    assert not opciones["3333TAL"].available
    assert "Taller" in opciones["3333TAL"].reason
    assert not opciones["4444OCU"].available
    assert otra.number in opciones["4444OCU"].reason
    # Los libres, primero.
    assert next(iter(opciones)) == "1234ABC"


def test_liberar_el_vehiculo_desde_la_ficha(client, reserva, coche, agente):
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    client.force_login(agente)

    respuesta = client.post(reverse("reservations:release_vehicle", args=[reserva.pk]))

    reserva.refresh_from_db()
    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    assert reserva.vehicle is None


def test_soltar_el_coche_no_cancela_la_reserva(reserva, coche, agente):
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)

    suelta = release_vehicle(reservation=reserva, actor=agente)

    assert suelta.status == ReservationStatus.PENDING
    assert Reservation.objects.filter(pk=reserva.pk).exists()


# ---------------------------------------------------------------------------
# Conductores adicionales
# ---------------------------------------------------------------------------


def _datos_conductor(**extra):
    datos = {
        "first_name": "Luis",
        "last_name": "Perez",
        "document_number": "12345678Z",
        "licence_number": "B-998877",
        "licence_country": "ES",
        "licence_expiry": (timezone.localdate() + timedelta(days=900)).isoformat(),
    }
    datos.update(extra)
    return datos


def test_alta_de_conductor_adicional(client, reserva, agente):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:driver_create", args=[reserva.pk]), _datos_conductor()
    )

    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    conductor = ReservationDriver.objects.get(reservation=reserva)
    assert conductor.last_name == "Perez"


def test_un_carnet_caducado_antes_de_la_devolucion_no_pasa(client, reserva, agente):
    """Caduca a mitad del alquiler: esa persona no puede devolver el coche."""
    client.force_login(agente)
    caduca = (timezone.localtime(reserva.return_at).date() - timedelta(days=1)).isoformat()

    respuesta = client.post(
        reverse("reservations:driver_create", args=[reserva.pk]),
        _datos_conductor(licence_expiry=caduca),
    )

    assert respuesta.status_code == 422
    assert not ReservationDriver.objects.exists()
    assert "caduca" in respuesta.content.decode()


def test_sin_caducidad_de_carnet_tampoco(client, reserva, agente):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:driver_create", args=[reserva.pk]),
        _datos_conductor(licence_expiry=""),
    )

    assert respuesta.status_code == 422
    assert not ReservationDriver.objects.exists()


def test_baja_de_conductor(client, reserva, agente):
    client.force_login(agente)
    client.post(reverse("reservations:driver_create", args=[reserva.pk]), _datos_conductor())
    conductor = ReservationDriver.objects.get()

    respuesta = client.post(reverse("reservations:driver_delete", args=[reserva.pk, conductor.pk]))

    assert respuesta.status_code == 200
    assert not ReservationDriver.objects.exists()


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------


def test_el_historial_junta_las_fuentes(reserva, agente, responsable):
    from apps.reservations.selectors import timeline
    from apps.reservations.state_machine import transition

    transition(reserva, ReservationStatus.CONFIRMED, agente)

    lineas = timeline(reserva)

    assert len(lineas) == 2  # el alta y la confirmacion
    assert lineas[0].happened_at >= lineas[1].happened_at, "de lo mas reciente a lo mas antiguo"
    assert lineas[0].source == "status"
    assert "Confirmada" in lineas[0].title


def test_la_pestana_de_historial_los_pinta(client, reserva, agente):
    client.force_login(agente)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "historial"])
    ).content.decode()

    assert "Alta rapida de mostrador" in contenido


def test_el_modal_ofrece_liberar_el_vehiculo_en_conflicto(
    client, reserva, economico, palma, coche, agente
):
    """Criterio de aceptacion, visto desde la pantalla.

    El POST falla por el coche, y lo que se le devuelve al mostrador no es solo
    el error: es el error con el boton que resuelve la situacion.
    """
    from apps.availability.services import assign_vehicle

    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    estorba = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(6),
        end=en(8),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:change_dates", args=[reserva.pk]),
        {
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "return_at": en(7).strftime("%Y-%m-%dT%H:%M"),
        },
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 422
    assert estorba.number in contenido
    assert coche.plate in contenido
    assert 'name="release_vehicle"' in contenido
    assert "Liberar el vehiculo" in contenido

    reserva.refresh_from_db()
    assert reserva.vehicle == coche, "nada se ha tocado todavia"


def test_al_liberar_desde_el_modal_el_cambio_se_aplica(
    client, reserva, economico, palma, coche, tarifa, agente
):
    from apps.availability.services import assign_vehicle
    from apps.fleet.tests.factories import VehicleFactory

    VehicleFactory(plate="5555LIB", category=economico, current_office=palma)
    assign_vehicle(reservation=reserva, vehicle=coche, actor=agente)
    reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(6),
        end=en(8),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:change_dates", args=[reserva.pk]),
        {
            "pickup_at": en(1).strftime("%Y-%m-%dT%H:%M"),
            "return_at": en(7).strftime("%Y-%m-%dT%H:%M"),
            "release_vehicle": "1",
        },
    )

    reserva.refresh_from_db()
    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    assert reserva.vehicle is None
    # No se compara con `en(7)` al minuto: el formulario manda hora local
    # (Europe/Madrid) y el helper va en UTC. Lo que importa es que se aplico.
    assert (reserva.return_at - reserva.pickup_at).days == 6


def test_cambiar_fechas_sin_permiso_da_403(client, reserva, solo_lectura):
    respuesta = client.get(reverse("reservations:change_dates", args=[reserva.pk]))
    assert respuesta.status_code == 302  # sin sesion

    client.force_login(solo_lectura)
    assert client.get(reverse("reservations:change_dates", args=[reserva.pk])).status_code == 403
    assert (
        client.post(reverse("reservations:change_dates", args=[reserva.pk]), {}).status_code == 403
    )
