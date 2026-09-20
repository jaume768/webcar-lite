"""Capacidad de categoria: lo que se puede aceptar y lo que no."""

import pytest

from apps.availability.services import (
    NoAvailabilityError,
    check_category_availability,
    get_available_categories,
    reserve_capacity,
)
from apps.fleet.tests.factories import VehicleBlockFactory
from apps.reservations.models import ReservationStatus
from apps.reservations.state_machine import transition

from .factories import en

pytestmark = pytest.mark.django_db


def _reservar(economico, centro, **kwargs):
    kwargs.setdefault("start", en(1))
    kwargs.setdefault("end", en(3))
    return reserve_capacity(category=economico, pickup_office=centro, **kwargs)


def test_con_tres_coches_la_cuarta_reserva_solapada_se_rechaza(economico, centro, tres_coches):
    for _ in range(3):
        _reservar(economico, centro)

    resultado = check_category_availability(economico, centro, en(1), en(3))
    assert resultado.total_fleet == 3
    assert resultado.occupied == 3
    assert resultado.free == 0
    assert not resultado.available

    with pytest.raises(NoAvailabilityError) as fallo:
        _reservar(economico, centro)

    assert "Economico" in str(fallo.value)
    assert fallo.value.result.free == 0


def test_al_cancelar_una_la_cuarta_pasa_a_ser_posible(economico, centro, tres_coches, responsable):
    reservas = [_reservar(economico, centro) for _ in range(3)]
    assert not check_category_availability(economico, centro, en(1), en(3)).available

    transition(reservas[0], ReservationStatus.CANCELLED, responsable)

    assert check_category_availability(economico, centro, en(1), en(3)).available
    assert _reservar(economico, centro).pk is not None


def test_un_vehiculo_bloqueado_no_cuenta_como_capacidad(economico, centro, tres_coches):
    """Taller: el coche existe, pero no se puede entregar."""
    VehicleBlockFactory(vehicle=tres_coches[0], start_at=en(0.5), end_at=en(5))

    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.total_fleet == 3
    assert resultado.blocked == 1
    assert resultado.free == 2
    assert resultado.available  # quedan dos, todavia se puede vender


def test_un_bloqueo_que_no_pisa_el_periodo_no_resta(economico, centro, tres_coches):
    VehicleBlockFactory(vehicle=tres_coches[0], start_at=en(10), end_at=en(12))

    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.blocked == 0
    assert resultado.free == 3


def test_el_estado_del_vehiculo_no_decide_el_futuro(economico, centro, tres_coches):
    """TALLER es de ahora; sin bloqueo con fechas, la semana que viene cuenta."""
    from apps.fleet.models import VehicleStatus

    coche = tres_coches[0]
    coche.status = VehicleStatus.WORKSHOP
    coche.save(update_fields=["status"])

    assert check_category_availability(economico, centro, en(20), en(22)).free == 3


def test_un_vehiculo_de_baja_no_es_flota(economico, centro, tres_coches):
    from apps.fleet.services import set_vehicle_active

    set_vehicle_active(vehicle=tres_coches[0], active=False)

    assert check_category_availability(economico, centro, en(1), en(3)).total_fleet == 2


def test_una_reserva_sin_vehiculo_consume_capacidad(economico, centro, un_coche):
    """La reserva va contra la categoria: no hace falta coche para ocupar hueco."""
    reserva = _reservar(economico, centro)
    assert reserva.vehicle is None

    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.reserved == 1
    assert resultado.free == 0
    assert not resultado.available


@pytest.mark.parametrize(
    ("estado", "resta"),
    [
        (ReservationStatus.DRAFT, False),
        (ReservationStatus.PENDING, True),
        (ReservationStatus.CONFIRMED, True),
        (ReservationStatus.IN_PROGRESS, True),
        (ReservationStatus.FINISHED, False),
        (ReservationStatus.CANCELLED, False),
        (ReservationStatus.NO_SHOW, False),
    ],
)
def test_que_estados_ocupan_flota(economico, centro, un_coche, estado, resta):
    from .factories import ReservationFactory

    ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
        status=estado,
        pickup_at=en(1),
        return_at=en(3),
    )

    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.available is not resta


def test_una_reserva_de_otra_categoria_no_estorba(economico, premium, centro, un_coche):
    from apps.fleet.tests.factories import VehicleFactory

    VehicleFactory(plate="5555PRE", category=premium, current_office=centro)
    _reservar(premium, centro)

    assert check_category_availability(economico, centro, en(1), en(3)).available


def test_sin_flota_en_el_grupo_no_hay_disponibilidad(economico, centro):
    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.total_fleet == 0
    assert not resultado.available
    assert "No hay vehiculos" in " ".join(resultado.blocking_reasons)


def test_get_available_categories_solo_ofrece_lo_vendible(economico, premium, centro, tres_coches):
    """Premium no tiene coches en el grupo: no se ofrece."""
    disponibles = get_available_categories(centro, en(1), en(3))

    codigos = [c.category.code for c in disponibles]
    assert "eco" in codigos
    assert "premium" not in codigos


def test_get_available_categories_puede_devolver_tambien_las_llenas(
    economico, premium, centro, tres_coches
):
    todas = get_available_categories(centro, en(1), en(3), include_unavailable=True)

    por_codigo = {c.category.code: c.availability for c in todas}
    assert por_codigo["eco"].available
    assert not por_codigo["premium"].available


def test_una_categoria_retirada_no_se_ofrece(economico, centro, tres_coches):
    from apps.fleet.services import set_category_active

    for coche in tres_coches:
        coche.is_active = False
        coche.save(update_fields=["is_active"])
    set_category_active(category=economico, active=False)

    disponibles = get_available_categories(centro, en(1), en(3), include_unavailable=True)

    assert [c.category.code for c in disponibles] == []
