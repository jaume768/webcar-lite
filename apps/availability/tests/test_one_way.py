"""One-way: dentro del mismo grupo de oficinas y entre grupos distintos.

El comportamiento esta razonado en docs/decisiones/ADR-001-disponibilidad.md.
En resumen: dentro del grupo el coche vuelve, entre grupos no vuelve, y ante la
duda preferimos no prometer un coche que no vamos a tener.
"""

import pytest

from apps.availability.services import (
    check_category_availability,
    pool_scope,
    reserve_capacity,
)
from apps.reservations.models import Reservation, ReservationStatus

from .factories import en

pytestmark = pytest.mark.django_db


def _cerrar(reserva):
    """Deja la reserva finalizada sin pasar por la maquina de estados.

    Aqui interesa el efecto sobre la capacidad, no el camino: las transiciones
    tienen su propio fichero de tests.
    """
    Reservation.objects.filter(pk=reserva.pk).update(status=ReservationStatus.FINISHED)


pytestmark = pytest.mark.django_db


# --- ambito -----------------------------------------------------------------


def test_el_ambito_de_una_oficina_con_grupo_son_todas_las_del_grupo(centro, aeropuerto):
    clave, oficinas = pool_scope(centro)

    assert clave == f"pool:{centro.pool_id}"
    assert set(oficinas) == {centro.id, aeropuerto.id}


def test_el_ambito_de_una_oficina_sin_grupo_es_ella_sola(suelta):
    clave, oficinas = pool_scope(suelta)

    assert clave == f"office:{suelta.id}"
    assert oficinas == [suelta.id]


def test_la_flota_del_grupo_cuenta_desde_cualquiera_de_sus_oficinas(
    economico, centro, aeropuerto, tres_coches
):
    """Los tres coches estan en el centro; el aeropuerto los ve como suyos."""
    assert check_category_availability(economico, aeropuerto, en(1), en(3)).total_fleet == 3


def test_una_oficina_sin_grupo_no_ve_la_flota_ajena(economico, suelta, tres_coches):
    assert check_category_availability(economico, suelta, en(1), en(3)).total_fleet == 0


# --- one-way dentro del grupo ----------------------------------------------


def test_dentro_del_grupo_el_coche_vuelve_y_libera_despues(economico, centro, aeropuerto, un_coche):
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=aeropuerto,
        start=en(1),
        end=en(3),
    )

    # Durante el alquiler el coche esta fuera: no hay capacidad.
    assert not check_category_availability(economico, centro, en(1), en(3)).available
    # Y tampoco desde la otra oficina del grupo: es la misma flota.
    assert not check_category_availability(economico, aeropuerto, en(1), en(3)).available
    # Despues vuelve a estar disponible, se recoja donde se recoja.
    assert check_category_availability(economico, centro, en(5), en(7)).available
    assert check_category_availability(economico, aeropuerto, en(5), en(7)).available


def test_dentro_del_grupo_da_igual_donde_se_recoja(economico, centro, aeropuerto, un_coche):
    reserve_capacity(
        category=economico,
        pickup_office=aeropuerto,
        return_office=centro,
        start=en(1),
        end=en(3),
    )

    assert not check_category_availability(economico, centro, en(1), en(3)).available


# --- one-way entre grupos ---------------------------------------------------


def test_entre_grupos_el_coche_no_vuelve_y_sigue_restando(economico, centro, lejana, un_coche):
    """Se lo llevan lejos: deja de ser capacidad de la ciudad."""
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=lejana,
        start=en(1),
        end=en(3),
    )

    assert not check_category_availability(economico, centro, en(1), en(3)).available
    # Y tampoco despues: el coche no ha vuelto ni va a volver solo.
    assert not check_category_availability(economico, centro, en(10), en(12)).available


def test_entre_grupos_no_estorba_a_lo_anterior_a_la_recogida(economico, centro, lejana, un_coche):
    """Antes de que salga, el coche sigue estando."""
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=lejana,
        start=en(10),
        end=en(12),
    )

    assert check_category_availability(economico, centro, en(1), en(3)).available


def test_el_grupo_de_destino_no_suma_el_coche_por_adelantado(economico, centro, lejana, un_coche):
    """Hasta que llegue fisicamente, Valencia no puede venderlo."""
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=lejana,
        start=en(1),
        end=en(3),
    )

    resultado = check_category_availability(economico, lejana, en(5), en(7))

    assert resultado.total_fleet == 0
    assert not resultado.available


def test_al_cerrar_la_reserva_deja_de_restar_en_origen(economico, centro, lejana, un_coche):
    """Sin esto, un coche trasladado penalizaria dos veces al grupo de origen."""
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=lejana,
        start=en(1),
        end=en(3),
    )
    assert not check_category_availability(economico, centro, en(10), en(12)).available

    _cerrar(reserva)

    # La reserva ya no ocupa. La capacidad del centro vuelve a depender solo de
    # donde este el coche, que es lo que actualiza la devolucion.
    assert check_category_availability(economico, centro, en(10), en(12)).available


def test_tras_el_traslado_el_coche_cuenta_en_el_grupo_de_destino(
    economico, centro, lejana, un_coche
):
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        return_office=lejana,
        start=en(1),
        end=en(3),
    )
    _cerrar(reserva)

    # La devolucion deja el coche aparcado en Valencia.
    un_coche.current_office = lejana
    un_coche.save(update_fields=["current_office"])

    assert check_category_availability(economico, lejana, en(5), en(7)).available
    assert not check_category_availability(economico, centro, en(5), en(7)).available
