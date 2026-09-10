"""Entrega y devolucion de principio a fin."""

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.fleet.models import VehicleStatus
from apps.operations.models import CheckIn, CheckOut, Damage, DamageZone, FuelLevel
from apps.operations.services import (
    OperationsServiceError,
    add_manual_charge,
    perform_check_in,
    perform_check_out,
    preexisting_damages,
)
from apps.reservations.models import ChargeKind, ReservationStatus
from apps.reservations.tests.factories import en

pytestmark = pytest.mark.django_db


def _entregar(reserva, empleado, **kwargs):
    kwargs.setdefault("mileage", 10_000)
    kwargs.setdefault("fuel_level", FuelLevel.FULL)
    kwargs.setdefault("licence_verified", True)
    kwargs.setdefault("id_verified", True)
    return perform_check_in(reservation=reserva, employee=empleado, **kwargs)


def _devolver(reserva, empleado, **kwargs):
    kwargs.setdefault("mileage", 10_500)
    kwargs.setdefault("fuel_level", FuelLevel.FULL)
    return perform_check_out(reservation=reserva, employee=empleado, **kwargs)


# ---------------------------------------------------------------------------
# Entrega
# ---------------------------------------------------------------------------


def test_la_entrega_pone_la_reserva_en_curso_y_el_coche_alquilado(reserva, coche, empleado):
    entrega = _entregar(reserva, empleado)

    reserva.refresh_from_db()
    coche.refresh_from_db()
    assert entrega.pk is not None
    assert reserva.status == ReservationStatus.IN_PROGRESS
    assert reserva.actual_pickup_at is not None
    assert coche.status == VehicleStatus.RENTED


def test_no_se_entrega_sin_vehiculo_asignado(reserva, empleado):
    """Criterio de aceptacion."""
    from apps.reservations.services import release_vehicle

    release_vehicle(reservation=reserva, actor=empleado)
    reserva.refresh_from_db()

    with pytest.raises(OperationsServiceError) as fallo:
        _entregar(reserva, empleado)

    assert "no tiene vehiculo asignado" in str(fallo.value)
    assert not CheckIn.objects.exists()
    reserva.refresh_from_db()
    assert reserva.status == ReservationStatus.CONFIRMED


def test_no_se_entrega_sin_comprobar_los_documentos(reserva, empleado):
    for carnet, dni in ((False, True), (True, False), (False, False)):
        with pytest.raises(OperationsServiceError) as fallo:
            _entregar(reserva, empleado, licence_verified=carnet, id_verified=dni)
        assert "carnet" in str(fallo.value)

    assert not CheckIn.objects.exists()


def test_no_se_entrega_dos_veces(reserva, empleado):
    _entregar(reserva, empleado)

    with pytest.raises(OperationsServiceError) as fallo:
        _entregar(reserva, empleado)

    assert "ya tiene entrega" in str(fallo.value)
    assert CheckIn.objects.count() == 1


def test_los_danos_de_la_entrega_son_preexistentes(reserva, empleado):
    _entregar(
        reserva,
        empleado,
        damages=[
            {
                "zone": DamageZone.FRONT_LEFT,
                "damage_type": "scratch",
                "severity": 1,
                "description": "Aranazo en la puerta",
                # Aunque venga marcado, un dano previo no se le cobra a este cliente.
                "charge_to_customer": True,
            }
        ],
    )

    dano = Damage.objects.get()
    assert dano.is_preexisting
    assert not dano.charge_to_customer


# ---------------------------------------------------------------------------
# Devolucion
# ---------------------------------------------------------------------------


def test_no_se_devuelve_sin_entrega_previa(reserva, empleado):
    """Criterio de aceptacion."""
    with pytest.raises(OperationsServiceError) as fallo:
        _devolver(reserva, empleado)

    assert "no tiene entrega registrada" in str(fallo.value)
    assert not CheckOut.objects.exists()


def test_la_devolucion_cierra_la_reserva_y_manda_el_coche_a_limpieza(reserva, coche, empleado):
    _entregar(reserva, empleado)

    _devolver(reserva, empleado)

    reserva.refresh_from_db()
    coche.refresh_from_db()
    assert reserva.status == ReservationStatus.FINISHED
    assert reserva.actual_return_at is not None
    assert coche.status == VehicleStatus.CLEANING


def test_el_estado_del_coche_al_devolver_es_configurable(reserva, coche, empleado, settings):
    settings.VEHICLE_STATUS_AFTER_CHECKOUT = VehicleStatus.AVAILABLE
    _entregar(reserva, empleado)

    _devolver(reserva, empleado)

    coche.refresh_from_db()
    assert coche.status == VehicleStatus.AVAILABLE


def test_devolver_en_otra_oficina_mueve_el_coche(reserva, coche, empleado, aeropuerto):
    """Criterio de aceptacion: current_office se actualiza."""
    _entregar(reserva, empleado)

    devolucion = _devolver(reserva, empleado, return_office=aeropuerto)

    coche.refresh_from_db()
    assert devolucion.return_office == aeropuerto
    assert coche.current_office == aeropuerto


def test_los_kilometros_no_pueden_bajar(reserva, empleado):
    _entregar(reserva, empleado, mileage=10_000)

    with pytest.raises(OperationsServiceError) as fallo:
        _devolver(reserva, empleado, mileage=9_000)

    assert "no pueden bajar" in str(fallo.value)
    assert not CheckOut.objects.exists()


def test_no_se_devuelve_dos_veces(reserva, empleado):
    _entregar(reserva, empleado)
    _devolver(reserva, empleado)

    with pytest.raises(OperationsServiceError):
        _devolver(reserva, empleado)

    assert CheckOut.objects.count() == 1


def test_los_danos_previos_llegan_precargados_a_la_devolucion(reserva, empleado):
    """Criterio de aceptacion."""
    _entregar(
        reserva,
        empleado,
        damages=[{"zone": DamageZone.REAR_BUMPER, "damage_type": "dent", "severity": 2}],
    )

    previos = list(preexisting_damages(reserva))

    assert len(previos) == 1
    assert previos[0].zone == DamageZone.REAR_BUMPER
    assert previos[0].is_preexisting


def test_los_danos_nuevos_se_distinguen_de_los_previos(reserva, empleado):
    _entregar(
        reserva,
        empleado,
        damages=[{"zone": DamageZone.REAR_BUMPER, "damage_type": "dent"}],
    )

    _devolver(
        reserva,
        empleado,
        damages=[
            {
                "zone": DamageZone.WINDSCREEN,
                "damage_type": "broken",
                "severity": 3,
                "estimated_cost": "250.00",
                "charge_to_customer": True,
            }
        ],
    )

    assert Damage.objects.filter(is_preexisting=True).count() == 1
    nuevo = Damage.objects.get(is_preexisting=False)
    assert nuevo.zone == DamageZone.WINDSCREEN
    assert nuevo.charge_to_customer
    assert nuevo.estimated_cost == Decimal("250.00")


# ---------------------------------------------------------------------------
# Cargos
# ---------------------------------------------------------------------------


def test_la_devolucion_limpia_no_genera_cargos(reserva, empleado):
    _entregar(reserva, empleado, mileage=10_000, fuel_level=FuelLevel.FULL)

    _devolver(reserva, empleado, mileage=10_200, fuel_level=FuelLevel.FULL)

    reserva.refresh_from_db()
    assert reserva.charges.count() == 0
    assert reserva.charges_total == Decimal("0.00")
    assert reserva.grand_total == reserva.total


def test_el_combustible_que_falta_se_cobra(reserva, empleado, coche):
    """Sale lleno, vuelve a la mitad: 25 L de 50 a 1,60 EUR mas IVA."""
    _entregar(reserva, empleado, fuel_level=FuelLevel.FULL)

    _devolver(reserva, empleado, fuel_level=FuelLevel.HALF)

    reserva.refresh_from_db()
    cargo = reserva.charges.get(kind=ChargeKind.FUEL)
    assert cargo.base_amount == Decimal("40.00")
    assert cargo.total == Decimal("48.40")  # 21% de IVA
    assert reserva.charges_total == Decimal("48.40")


def test_los_kilometros_de_mas_se_cobran(reserva, empleado):
    reserva.included_km = 1000
    reserva.save(update_fields=["included_km"])
    _entregar(reserva, empleado, mileage=10_000)

    _devolver(reserva, empleado, mileage=11_200)

    reserva.refresh_from_db()
    cargo = reserva.charges.get(kind=ChargeKind.EXTRA_KM)
    assert cargo.base_amount == Decimal("30.00")


def test_la_devolucion_tardia_se_cobra(reserva, empleado):
    _entregar(reserva, empleado)

    _devolver(reserva, empleado, actual_datetime=reserva.return_at + timedelta(hours=3))

    reserva.refresh_from_db()
    cargo = reserva.charges.get(kind=ChargeKind.LATE_RETURN)
    assert cargo.quantity == Decimal("1")
    assert cargo.base_amount == Decimal("45.00")


def test_los_cargos_suben_el_pendiente_de_cobro(reserva, empleado):
    from apps.billing.selectors import pending_amount

    _entregar(reserva, empleado, fuel_level=FuelLevel.FULL)
    antes = pending_amount(reserva)

    _devolver(reserva, empleado, fuel_level=FuelLevel.HALF)

    reserva.refresh_from_db()
    assert pending_amount(reserva) == antes + Decimal("48.40")


def test_cargo_manual_de_limpieza(reserva, empleado):
    _entregar(reserva, empleado)
    _devolver(reserva, empleado)

    add_manual_charge(
        reservation=reserva,
        kind=ChargeKind.CLEANING,
        concept="Limpieza especial: arena en el maletero",
        amount=Decimal("30.00"),
        employee=empleado,
    )

    reserva.refresh_from_db()
    cargo = reserva.charges.get(kind=ChargeKind.CLEANING)
    assert not cargo.is_automatic
    assert cargo.total == Decimal("36.30")
    assert reserva.charges_total == Decimal("36.30")


def test_un_cargo_manual_sin_concepto_no_pasa(reserva, empleado):
    with pytest.raises(OperationsServiceError):
        add_manual_charge(
            reservation=reserva,
            kind=ChargeKind.OTHER,
            concept="   ",
            amount=Decimal("10.00"),
            employee=empleado,
        )


def test_los_cargos_de_la_devolucion_pueden_venir_a_mano(reserva, empleado):
    _entregar(reserva, empleado)

    _devolver(
        reserva,
        empleado,
        manual_charges=[
            {"kind": ChargeKind.DAMAGE, "concept": "Paragolpes rayado", "amount": "150.00"}
        ],
    )

    reserva.refresh_from_db()
    cargo = reserva.charges.get(kind=ChargeKind.DAMAGE)
    assert cargo.base_amount == Decimal("150.00")
    assert not cargo.is_automatic


def test_un_recalculo_de_precio_no_borra_los_cargos(reserva, empleado):
    """Los cargos van aparte del precio del alquiler a proposito."""
    from apps.reservations.services import recalculate_price

    _entregar(reserva, empleado, fuel_level=FuelLevel.FULL)
    _devolver(reserva, empleado, fuel_level=FuelLevel.HALF)
    reserva.refresh_from_db()

    recalculate_price(reservation=reserva, actor=empleado)

    reserva.refresh_from_db()
    assert reserva.charges_total == Decimal("48.40")
    assert reserva.grand_total == reserva.total + Decimal("48.40")


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------


def test_la_pestana_de_entrega_avisa_si_no_hay_coche(client, reserva, empleado):
    from django.urls import reverse

    from apps.reservations.services import release_vehicle

    release_vehicle(reservation=reserva, actor=empleado)
    client.force_login(empleado)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "checkin"])
    ).content.decode()

    assert "no tiene vehiculo asignado" in contenido
    assert reverse("operations:check_in", args=[reserva.pk]) not in contenido


def test_la_pestana_de_devolucion_avisa_si_no_hay_entrega(client, reserva, empleado):
    from django.urls import reverse

    client.force_login(empleado)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "checkout"])
    ).content.decode()

    assert "no consta entregado" in contenido
    assert reverse("operations:check_out", args=[reserva.pk]) not in contenido


def test_la_entrega_desde_la_pantalla(client, reserva, coche, empleado):
    from django.urls import reverse

    client.force_login(empleado)

    respuesta = client.post(
        reverse("operations:check_in", args=[reserva.pk]),
        {
            "actual_datetime": en(1).strftime("%Y-%m-%dT%H:%M"),
            "mileage": 10_000,
            "fuel_level": FuelLevel.FULL,
            "observations": "",
            "licence_verified": "on",
            "id_verified": "on",
        },
    )

    reserva.refresh_from_db()
    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    assert reserva.status == ReservationStatus.IN_PROGRESS


def test_la_entrega_sin_marcar_documentos_se_queda_en_el_formulario(client, reserva, empleado):
    from django.urls import reverse

    client.force_login(empleado)

    respuesta = client.post(
        reverse("operations:check_in", args=[reserva.pk]),
        {
            "actual_datetime": en(1).strftime("%Y-%m-%dT%H:%M"),
            "mileage": 10_000,
            "fuel_level": FuelLevel.FULL,
            "observations": "",
        },
    )

    assert respuesta.status_code == 422
    assert "documentos" in respuesta.content.decode()
    assert not CheckIn.objects.exists()


def test_el_parte_de_danos_antes_de_entregar_es_preexistente(client, reserva, empleado):
    from django.urls import reverse

    client.force_login(empleado)

    respuesta = client.post(
        reverse("operations:damage_create", args=[reserva.pk]),
        {
            "zone": DamageZone.FRONT_LEFT,
            "damage_type": "scratch",
            "severity": 1,
            "description": "Aranazo",
            "estimated_cost": "0.00",
            "charge_to_customer": "on",
        },
    )

    assert respuesta.status_code == 200
    dano = Damage.objects.get()
    assert dano.is_preexisting
    assert not dano.charge_to_customer, "un dano previo no se repercute"


def test_el_parte_de_danos_despues_de_entregar_es_nuevo(client, reserva, empleado):
    from django.urls import reverse

    _entregar(reserva, empleado)
    reserva.refresh_from_db()
    client.force_login(empleado)

    client.post(
        reverse("operations:damage_create", args=[reserva.pk]),
        {
            "zone": DamageZone.WINDSCREEN,
            "damage_type": "broken",
            "severity": 3,
            "description": "Impacto",
            "estimated_cost": "120.00",
            "charge_to_customer": "on",
        },
    )

    dano = Damage.objects.get()
    assert not dano.is_preexisting
    assert dano.charge_to_customer


def test_la_devolucion_desde_la_pantalla_calcula_los_cargos(
    client, reserva, coche, empleado, aeropuerto
):
    from django.urls import reverse

    _entregar(reserva, empleado, mileage=10_000, fuel_level=FuelLevel.FULL)
    reserva.refresh_from_db()
    client.force_login(empleado)

    respuesta = client.post(
        reverse("operations:check_out", args=[reserva.pk]),
        {
            "actual_datetime": en(4).strftime("%Y-%m-%dT%H:%M"),
            "mileage": 10_400,
            "fuel_level": FuelLevel.HALF,
            "return_office": aeropuerto.pk,
            "observations": "",
            "cleaning_charge": "20.00",
            "damage_charge": "",
            "damage_concept": "",
        },
    )

    reserva.refresh_from_db()
    coche.refresh_from_db()
    assert respuesta.status_code == 200
    assert reserva.status == ReservationStatus.FINISHED
    assert coche.current_office == aeropuerto
    conceptos = set(reserva.charges.values_list("kind", flat=True))
    assert ChargeKind.FUEL in conceptos
    assert ChargeKind.CLEANING in conceptos


def test_recoger_antes_de_hora_no_inventa_dias_de_retraso(reserva, empleado):
    """El cargo mide el retraso de la devolucion, no cuando se recogio.

    Si el coche sale dos dias antes de lo previsto y se devuelve a su hora, no
    hay nada que cobrar: lo que se facturo fue el periodo previsto.
    """
    _entregar(reserva, empleado, actual_datetime=reserva.pickup_at - timedelta(days=2))

    _devolver(reserva, empleado, actual_datetime=reserva.return_at)

    reserva.refresh_from_db()
    assert not reserva.charges.filter(kind=ChargeKind.LATE_RETURN).exists()
