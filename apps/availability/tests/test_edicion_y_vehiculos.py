"""Revalidacion al editar, asignacion de vehiculo y bajas de flota."""

import pytest
from django.db import IntegrityError, transaction

from apps.availability.services import (
    NoAvailabilityError,
    VehicleNotAvailableError,
    assign_vehicle,
    check_category_availability,
    check_vehicle_availability,
    get_available_vehicles,
    reserve_capacity,
    update_reservation_period,
)
from apps.fleet.services import set_vehicle_active
from apps.fleet.tests.factories import VehicleBlockFactory
from apps.reservations.models import Reservation, ReservationStatus

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db


# --- exclude_reservation ----------------------------------------------------


def test_una_reserva_no_se_cuenta_a_si_misma_al_cambiar_fechas(economico, palma, un_coche):
    """Con el unico coche cogido por ella misma, alargarla tiene que poder."""
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    sin_excluir = check_category_availability(economico, palma, en(1), en(5))
    excluyendola = check_category_availability(
        economico, palma, en(1), en(5), exclude_reservation=reserva
    )

    assert not sin_excluir.available
    assert excluyendola.available

    ampliada = update_reservation_period(reservation=reserva, end=en(5))
    assert ampliada.return_at == en(5)


def test_exclude_reservation_acepta_tambien_la_clave(economico, palma, un_coche):
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    resultado = check_category_availability(
        economico, palma, en(1), en(3), exclude_reservation=reserva.pk
    )

    assert resultado.available


def test_alargar_cuando_el_coche_ya_esta_vendido_a_otro_se_rechaza(economico, palma, un_coche):
    """El mensaje tiene que decir quien estorba, no un 'no se puede' a secas."""
    en_curso = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        vehicle=un_coche,
        status=ReservationStatus.IN_PROGRESS,
    )
    siguiente = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(4),
        end=en(6),
        vehicle=un_coche,
        status=ReservationStatus.CONFIRMED,
    )

    with pytest.raises(VehicleNotAvailableError) as fallo:
        update_reservation_period(reservation=en_curso, end=en(5))

    mensaje = str(fallo.value)
    assert siguiente.number in mensaje, mensaje
    assert un_coche.plate in mensaje

    en_curso.refresh_from_db()
    assert en_curso.return_at == en(3), "no se ha guardado nada"


def test_alargar_sin_conflicto_si_hay_hueco(economico, palma, un_coche):
    reserva = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        vehicle=un_coche,
        status=ReservationStatus.IN_PROGRESS,
    )

    ampliada = update_reservation_period(reservation=reserva, end=en(3.5))

    assert ampliada.return_at == en(3.5)


def test_al_cambiar_de_categoria_se_revalida_la_nueva(economico, premium, palma, un_coche):
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    with pytest.raises(NoAvailabilityError):
        update_reservation_period(reservation=reserva, category=premium)


# --- asignacion de vehiculo -------------------------------------------------


def test_asignar_un_coche_libre(economico, palma, tres_coches):
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    asignada = assign_vehicle(reservation=reserva, vehicle=tres_coches[0])

    assert asignada.vehicle == tres_coches[0]


def test_asignar_un_coche_ya_comprometido_se_rechaza(economico, palma, tres_coches):
    ocupada = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        vehicle=tres_coches[0],
    )
    otra = reserve_capacity(category=economico, pickup_office=palma, start=en(2), end=en(4))

    with pytest.raises(VehicleNotAvailableError) as fallo:
        assign_vehicle(reservation=otra, vehicle=tres_coches[0])

    assert ocupada.number in str(fallo.value)


def test_asignar_un_coche_de_otra_categoria_se_rechaza(economico, premium, palma, tres_coches):
    from apps.fleet.tests.factories import VehicleFactory

    ajeno = VehicleFactory(plate="7777PRE", category=premium, current_office=palma)
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    with pytest.raises(VehicleNotAvailableError):
        assign_vehicle(reservation=reserva, vehicle=ajeno)


def test_saltarse_el_servicio_choca_contra_la_base_de_datos(economico, palma, un_coche):
    """La constraint de exclusion es la ultima red: escribir a pelo no cuela."""
    ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        vehicle=un_coche,
        pickup_at=en(1),
        return_at=en(3),
        status=ReservationStatus.CONFIRMED,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ReservationFactory(
            category=economico,
            pickup_office=palma,
            return_office=palma,
            vehicle=un_coche,
            pickup_at=en(2),
            return_at=en(4),
            status=ReservationStatus.CONFIRMED,
        )


def test_una_cancelada_no_bloquea_el_mismo_coche(economico, palma, un_coche):
    """El WHERE parcial de la constraint deja fuera lo que no ocupa flota."""
    ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        vehicle=un_coche,
        pickup_at=en(1),
        return_at=en(3),
        status=ReservationStatus.CANCELLED,
    )

    viva = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        vehicle=un_coche,
        pickup_at=en(1),
        return_at=en(3),
        status=ReservationStatus.CONFIRMED,
    )

    assert viva.pk is not None


def test_check_vehicle_availability(economico, palma, un_coche):
    assert check_vehicle_availability(un_coche, en(1), en(3))

    reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        vehicle=un_coche,
    )

    assert not check_vehicle_availability(un_coche, en(2), en(4))
    assert check_vehicle_availability(un_coche, en(10), en(12))


def test_get_available_vehicles_descarta_ocupados_y_bloqueados(economico, palma, tres_coches):
    reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        vehicle=tres_coches[0],
    )
    VehicleBlockFactory(vehicle=tres_coches[1], start_at=en(0.5), end_at=en(5))

    libres = list(get_available_vehicles(economico, palma, en(1), en(3)))

    assert libres == [tres_coches[2]]


# --- baja de flota ----------------------------------------------------------


def test_dar_de_baja_un_coche_deja_sus_reservas_para_reasignar(economico, palma, tres_coches):
    """La reserva no se pierde: el cliente sigue teniendo su categoria."""
    coche = tres_coches[0]
    futura = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(5),
        end=en(7),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )

    set_vehicle_active(vehicle=coche, active=False)

    futura.refresh_from_db()
    assert Reservation.objects.filter(pk=futura.pk).exists()
    assert futura.vehicle is None
    assert futura.needs_reassignment
    assert futura.status == ReservationStatus.CONFIRMED


def test_la_baja_no_toca_el_historico(economico, palma, tres_coches):
    """Una reserva ya terminada con ese coche es historia y se queda como esta."""
    coche = tres_coches[0]
    pasada = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        vehicle=coche,
        pickup_at=en(-10),
        return_at=en(-8),
        status=ReservationStatus.FINISHED,
    )

    set_vehicle_active(vehicle=coche, active=False)

    pasada.refresh_from_db()
    assert pasada.vehicle == coche
    assert not pasada.needs_reassignment


def test_tras_la_baja_la_reserva_se_puede_reasignar(economico, palma, tres_coches):
    coche = tres_coches[0]
    futura = reserve_capacity(
        category=economico,
        pickup_office=palma,
        start=en(5),
        end=en(7),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )
    set_vehicle_active(vehicle=coche, active=False)
    futura.refresh_from_db()

    reasignada = assign_vehicle(reservation=futura, vehicle=tres_coches[1])

    assert reasignada.vehicle == tres_coches[1]
    assert not reasignada.needs_reassignment
