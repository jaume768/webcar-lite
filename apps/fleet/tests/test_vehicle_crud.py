"""Vehiculos: alta, matricula unica, scope de oficina y estados."""

import pytest
from django.db import IntegrityError, transaction
from django.urls import NoReverseMatch, reverse

from apps.core.models import PhysicalDeleteNotAllowed
from apps.fleet.models import Vehicle, VehicleStatus
from apps.fleet.services import (
    FleetServiceError,
    finish_rental,
    set_vehicle_active,
    set_vehicle_status,
    start_rental,
)

from .factories import VehicleCategoryFactory, VehicleFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def datos_vehiculo(categoria, oficina, **cambios):
    datos = {
        "plate": "1234ABC",
        "brand": "Seat",
        "model": "Leon",
        "version": "",
        "category": categoria.pk,
        "current_office": oficina.pk,
        "vin": "",
        "mileage": 15000,
        "fuel": "diesel",
        "transmission": "manual",
        "seats": 5,
        "color": "Blanco",
        "registration_date": "",
        "itv_expiry": "",
        "insurance_expiry": "",
        "insurance_company": "",
        "insurance_policy": "",
        "purchase_date": "",
        "notes": "",
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Alta y matricula
# ---------------------------------------------------------------------------


def test_el_alta_crea_el_vehiculo(client, gestor_maestros, centro):
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:vehicle_create"), datos_vehiculo(categoria, centro), headers=HTMX
    )

    assert respuesta.status_code == 200
    assert Vehicle.objects.get(plate="1234ABC").current_office == centro


def test_dos_vehiculos_no_comparten_matricula(client, gestor_maestros, centro):
    """Criterio de aceptacion."""
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    VehicleFactory(plate="1234ABC", category=categoria, current_office=centro)
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:vehicle_create"), datos_vehiculo(categoria, centro), headers=HTMX
    )

    assert respuesta.status_code == 422
    assert "Ya hay un vehiculo con esa matricula." in respuesta.content.decode()
    assert Vehicle.objects.filter(plate="1234ABC").count() == 1


def test_la_matricula_es_unica_tambien_en_la_base_de_datos(centro):
    """El formulario no es la unica defensa: la columna es UNIQUE."""
    VehicleFactory(plate="1234ABC", current_office=centro)

    with pytest.raises(IntegrityError), transaction.atomic():
        Vehicle.objects.create(
            plate="1234ABC",
            brand="Otro",
            model="Coche",
            category=VehicleCategoryFactory(code="otra"),
            current_office=centro,
            fuel="petrol",
            transmission="manual",
        )


def test_la_matricula_se_normaliza(client, gestor_maestros, centro):
    """'1234-abc' y '1234ABC' son el mismo coche."""
    categoria = VehicleCategoryFactory(code="eco")
    client.force_login(gestor_maestros)

    client.post(
        reverse("fleet:vehicle_create"),
        datos_vehiculo(categoria, centro, plate="1234-abc"),
        headers=HTMX,
    )

    assert Vehicle.objects.filter(plate="1234ABC").exists()


# ---------------------------------------------------------------------------
# Scope de oficina
# ---------------------------------------------------------------------------


def test_solo_se_ve_la_flota_de_tus_oficinas(client, gestor_maestros, centro, norte):
    VehicleFactory(plate="1111AAA", current_office=centro)
    VehicleFactory(plate="2222BBB", current_office=norte)
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("fleet:vehicle_list")).content.decode()

    assert "1111AAA" in contenido
    assert "2222BBB" not in contenido


def test_un_coche_de_otra_oficina_no_se_edita_ni_por_url(client, gestor_maestros, norte):
    """404 y no 403: un 403 confirmaria que ese coche existe."""
    ajeno = VehicleFactory(plate="2222BBB", current_office=norte)
    client.force_login(gestor_maestros)

    respuesta = client.get(reverse("fleet:vehicle_update", args=[ajeno.pk]), headers=HTMX)

    assert respuesta.status_code == 404


def test_no_se_puede_aparcar_un_coche_en_una_oficina_ajena(client, gestor_maestros, norte):
    from apps.fleet.forms import VehicleForm

    formulario = VehicleForm(user=gestor_maestros)

    assert norte not in formulario.fields["current_office"].queryset


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------


def test_un_estado_manual_se_puede_poner(centro):
    vehiculo = VehicleFactory(current_office=centro)

    set_vehicle_status(vehicle=vehiculo, status=VehicleStatus.WORKSHOP)
    vehiculo.refresh_from_db()

    assert vehiculo.status == VehicleStatus.WORKSHOP


@pytest.mark.parametrize("estado", [VehicleStatus.RENTED, VehicleStatus.RESERVED])
def test_los_estados_de_la_operativa_no_se_ponen_a_mano(centro, estado):
    """Alquilado y reservado los escribe el proceso, no la ficha."""
    vehiculo = VehicleFactory(current_office=centro)

    with pytest.raises(FleetServiceError, match="operativa"):
        set_vehicle_status(vehicle=vehiculo, status=estado)


def test_un_coche_alquilado_no_pasa_a_disponible_a_mano(centro):
    """Criterio del prompt: el estado no puede contradecir a la realidad."""
    vehiculo = VehicleFactory(current_office=centro)
    start_rental(vehicle=vehiculo)
    vehiculo.refresh_from_db()

    with pytest.raises(FleetServiceError, match="devolucion"):
        set_vehicle_status(vehicle=vehiculo, status=VehicleStatus.AVAILABLE)

    vehiculo.refresh_from_db()
    assert vehiculo.status == VehicleStatus.RENTED


def test_el_ciclo_de_entrega_y_devolucion(centro):
    """El check-in lo pone en alquilado; el check-out lo devuelve."""
    vehiculo = VehicleFactory(current_office=centro, mileage=10_000)

    start_rental(vehicle=vehiculo)
    vehiculo.refresh_from_db()
    assert vehiculo.status == VehicleStatus.RENTED

    finish_rental(vehicle=vehiculo, mileage=10_450)
    vehiculo.refresh_from_db()
    assert vehiculo.status == VehicleStatus.CLEANING
    assert vehiculo.mileage == 10_450


def test_los_kilometros_no_pueden_bajar(centro):
    vehiculo = VehicleFactory(current_office=centro, mileage=10_000)
    start_rental(vehicle=vehiculo)

    with pytest.raises(FleetServiceError, match="kilometros"):
        finish_rental(vehicle=vehiculo, mileage=9_000)


def test_no_se_devuelve_un_coche_que_no_estaba_entregado(centro):
    vehiculo = VehicleFactory(current_office=centro)

    with pytest.raises(FleetServiceError, match="no consta entregado"):
        finish_rental(vehicle=vehiculo)


def test_un_coche_con_bloqueo_vivo_no_pasa_a_disponible(centro):
    from datetime import timedelta

    from django.utils import timezone

    from .factories import VehicleBlockFactory

    vehiculo = VehicleFactory(current_office=centro, status=VehicleStatus.WORKSHOP)
    ahora = timezone.now()
    VehicleBlockFactory(
        vehicle=vehiculo, start_at=ahora - timedelta(hours=1), end_at=ahora + timedelta(days=1)
    )

    with pytest.raises(FleetServiceError, match="bloqueo activo"):
        set_vehicle_status(vehicle=vehiculo, status=VehicleStatus.AVAILABLE)


def test_la_vista_de_estado_avisa_en_vez_de_reventar(client, gestor_maestros, centro):
    vehiculo = VehicleFactory(current_office=centro)
    start_rental(vehicle=vehiculo)
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:vehicle_status", args=[vehiculo.pk]),
        {"status": VehicleStatus.AVAILABLE},
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "devolucion" in respuesta.content.decode()


# ---------------------------------------------------------------------------
# Baja
# ---------------------------------------------------------------------------


def test_la_baja_deja_el_coche_en_estado_baja(centro):
    vehiculo = VehicleFactory(current_office=centro)

    set_vehicle_active(vehicle=vehiculo, active=False)
    vehiculo.refresh_from_db()

    assert vehiculo.is_active is False
    assert vehiculo.status == VehicleStatus.RETIRED


def test_no_se_da_de_baja_un_coche_que_esta_fuera(centro):
    vehiculo = VehicleFactory(current_office=centro)
    start_rental(vehicle=vehiculo)

    with pytest.raises(FleetServiceError, match="no se puede dar de baja"):
        set_vehicle_active(vehicle=vehiculo, active=False)


def test_un_vehiculo_no_se_borra(centro):
    vehiculo = VehicleFactory(current_office=centro)

    with pytest.raises(PhysicalDeleteNotAllowed):
        vehiculo.delete()


def test_no_hay_ruta_de_borrado():
    with pytest.raises(NoReverseMatch):
        reverse("fleet:vehicle_delete", args=[1])


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("fleet:vehicle_list", "get", False),
    ("fleet:vehicle_create", "get", False),
    ("fleet:vehicle_create", "post", False),
    ("fleet:vehicle_update", "get", True),
    ("fleet:vehicle_status", "post", True),
    ("fleet:vehicle_activate", "post", True),
    ("fleet:vehicle_deactivate", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, centro, vista, metodo, con_objeto):
    vehiculo = VehicleFactory(current_office=centro)
    client.force_login(agente_centro)

    url = reverse(vista, args=[vehiculo.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403
