"""Auditoria del motor de disponibilidad y sus interacciones con reservas.

Estos tests no prueban funcionalidad nueva: reproducen los seis escenarios de
la revision y dejan escrito el comportamiento real de hoy. Los marcados con
`xfail` son defectos confirmados; cuando se arreglen pasaran a XPASS y habra
que convertirlos en asserts normales.
"""

import pytest

from apps.availability.services import (
    VehicleNotAvailableError,
    assign_vehicle,
    check_category_availability,
    reserve_capacity,
    update_reservation_period,
)
from apps.fleet.tests.factories import VehicleBlockFactory, VehicleFactory
from apps.reservations.models import Reservation, ReservationStatus

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. Coche averiado con reserva confirmada encima
# ---------------------------------------------------------------------------


def test_bloquear_un_coche_no_avisa_de_la_reserva_que_lo_tenia(economico, centro, tres_coches):
    """Se manda al taller un coche que ya estaba comprometido: nadie se entera."""
    coche = tres_coches[0]
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=coche,
        status=ReservationStatus.CONFIRMED,
    )

    # Averia el dia antes de la entrega: el taller lo bloquea sin mas.
    VehicleBlockFactory(vehicle=coche, start_at=en(1), end_at=en(6))

    reserva.refresh_from_db()
    assert reserva.vehicle == coche, "la reserva sigue apuntando al coche averiado"
    assert not reserva.needs_reassignment, "y no queda marcada para reasignar"


def test_la_capacidad_pierde_la_demanda_de_una_reserva_con_coche_bloqueado(
    economico, centro, tres_coches
):
    """DEFECTO: al bloquear el coche de una reserva, su demanda desaparece.

    Con tres coches y dos reservas (una de ellas con el coche averiado), lo
    honesto es que queden cero huecos: el cliente de la reserva averiada sigue
    necesitando un coche y hay que reasignarlo. El motor, en cambio, deja uno
    libre y lo vuelve a vender.
    """
    con_coche = tres_coches[0]
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=con_coche,
        status=ReservationStatus.CONFIRMED,
    )
    reserve_capacity(category=economico, pickup_office=centro, start=en(2), end=en(5))

    VehicleBlockFactory(vehicle=con_coche, start_at=en(1), end_at=en(6))

    resultado = check_category_availability(economico, centro, en(2), en(5))

    assert resultado.total_fleet == 3
    assert resultado.blocked == 1
    # La reserva del coche averiado se cae de la cuenta: solo se ve la otra.
    assert resultado.reserved == 1
    assert resultado.free == 1, "un hueco que en realidad no existe"


@pytest.mark.xfail(
    reason="DEFECTO: la demanda de una reserva cuyo coche esta bloqueado no se cuenta",
    strict=True,
)
def test_no_deberia_quedar_hueco_libre(economico, centro, tres_coches):
    con_coche = tres_coches[0]
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=con_coche,
        status=ReservationStatus.CONFIRMED,
    )
    reserve_capacity(category=economico, pickup_office=centro, start=en(2), end=en(5))
    VehicleBlockFactory(vehicle=con_coche, start_at=en(1), end_at=en(6))

    # Tres coches, uno en taller, dos reservas vivas: no queda nada que vender.
    assert check_category_availability(economico, centro, en(2), en(5)).free == 0


# ---------------------------------------------------------------------------
# 2. Coche de otro pool asignado a una reserva
# ---------------------------------------------------------------------------


def test_se_puede_asignar_un_coche_de_otro_grupo_de_oficinas(economico, centro, lejana, un_coche):
    """DEFECTO: `assign_vehicle` no mira en que grupo esta aparcado el coche."""
    de_lejana = VehicleFactory(plate="9000VLC", category=economico, current_office=lejana)
    reserva = reserve_capacity(category=economico, pickup_office=centro, start=en(2), end=en(5))

    asignada = assign_vehicle(reservation=reserva, vehicle=de_lejana)

    assert asignada.vehicle == de_lejana, "acepta un coche que esta en otra zona"


def test_un_coche_comprometido_fuera_sigue_contando_como_capacidad_propia(
    economico, centro, lejana, un_coche
):
    """DEFECTO: el centro cuenta un coche suyo que esta vendido desde otra zona."""
    reserva_de_valencia = ReservationFactory(
        category=economico,
        pickup_office=lejana,
        return_office=lejana,
        pickup_at=en(2),
        return_at=en(5),
    )
    assign_vehicle(reservation=reserva_de_valencia, vehicle=un_coche)

    resultado = check_category_availability(economico, centro, en(2), en(5))

    assert resultado.total_fleet == 1
    assert resultado.reserved == 0, "la reserva de la otra zona no resta en el centro"
    assert resultado.free == 1, "el centro cree tener libre un coche ya comprometido"


# ---------------------------------------------------------------------------
# 3. Vehiculo de categoria equivocada al crear
# ---------------------------------------------------------------------------


def test_reserve_capacity_acepta_un_coche_de_otra_categoria(economico, premium, centro, un_coche):
    """DEFECTO: `assign_vehicle` lo comprueba y `reserve_capacity` no."""
    de_premium = VehicleFactory(plate="8000PRE", category=premium, current_office=centro)

    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=de_premium,
        status=ReservationStatus.CONFIRMED,
    )

    assert reserva.vehicle.category == premium
    assert reserva.category == economico, "reserva de economico con un coche premium"


# ---------------------------------------------------------------------------
# 4. Prolongar el alquiler con el coche ya vendido a otro
# ---------------------------------------------------------------------------


def test_prolongar_se_rechaza_nombrando_la_reserva_en_conflicto(economico, centro, un_coche):
    en_curso = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(1),
        end=en(3),
        vehicle=un_coche,
        status=ReservationStatus.IN_PROGRESS,
    )
    siguiente = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(5),
        end=en(7),
        vehicle=un_coche,
        status=ReservationStatus.CONFIRMED,
    )

    with pytest.raises(VehicleNotAvailableError) as fallo:
        update_reservation_period(reservation=en_curso, end=en(6))

    assert siguiente.number in str(fallo.value)
    en_curso.refresh_from_db()
    assert en_curso.return_at == en(3)


def test_prolongar_no_se_bloquea_a_si_misma(economico, centro, un_coche):
    """Con el unico coche cogido por ella misma, alargarla tiene que poder."""
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(1),
        end=en(3),
        vehicle=un_coche,
        status=ReservationStatus.IN_PROGRESS,
    )

    ampliada = update_reservation_period(reservation=reserva, end=en(6))

    assert ampliada.return_at == en(6)


# ---------------------------------------------------------------------------
# 5. Cambio de oficina de devolucion a otro pool
# ---------------------------------------------------------------------------


def test_cambiar_la_devolucion_a_otro_pool_no_revalida_la_capacidad_de_origen(
    economico, centro, lejana, un_coche
):
    """DEFECTO: el one-way entre grupos aparece sin que nadie recuente.

    Al mover la devolucion a otro grupo, la reserva pasa a ocupar el coche de
    el centro **sin fecha de fin**. Eso cambia su capacidad para todo lo
    que venga despues, y la reserva se guarda sin comprobar nada de eso.
    """
    reserva = reserve_capacity(category=economico, pickup_office=centro, start=en(2), end=en(5))
    otra_despues = check_category_availability(economico, centro, en(10), en(12))
    assert otra_despues.available, "antes del cambio, el centro tiene el coche libre luego"

    update_reservation_period(reservation=reserva, return_office=lejana)

    despues = check_category_availability(economico, centro, en(10), en(12))
    assert not despues.available, "ahora el coche no vuelve, y eso no se aviso"


def test_apply_change_no_permite_cambiar_la_oficina_de_devolucion(
    economico, centro, lejana, un_coche
):
    """DEFECTO: la ficha no puede cambiar la devolucion; el parametro no existe."""
    import inspect

    from apps.reservations.services import apply_change, preview_change

    assert "return_office" not in inspect.signature(apply_change).parameters
    assert "return_office" not in inspect.signature(preview_change).parameters


# ---------------------------------------------------------------------------
# 6. Reserva sin coche que llega al dia de la entrega
# ---------------------------------------------------------------------------


def test_una_reserva_sin_coche_no_se_puede_entregar(economico, centro, un_coche, responsable):
    from django.contrib.auth.models import Permission

    from apps.reservations.state_machine import TransitionRefused, transition

    responsable.role.permissions.add(
        Permission.objects.get(
            content_type__app_label="reservations", codename="change_reservation"
        )
    )
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(0.1),
        end=en(3),
        status=ReservationStatus.CONFIRMED,
    )

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.IN_PROGRESS, responsable)

    assert "vehiculo" in str(fallo.value)


def test_una_reserva_sin_coche_sigue_ocupando_capacidad(economico, centro, un_coche):
    """Lo correcto: aunque no tenga coche concreto, el hueco esta vendido."""
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(0.1),
        end=en(3),
        status=ReservationStatus.CONFIRMED,
    )

    assert not check_category_availability(economico, centro, en(0.1), en(3)).available


# ---------------------------------------------------------------------------
# 7. Estados que consumen y liberan
# ---------------------------------------------------------------------------


def test_una_reserva_en_curso_vencida_sigue_ocupando(economico, centro, un_coche):
    """Un coche que no ha vuelto sigue fuera, aunque la fecha ya pasara."""
    ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
        vehicle=un_coche,
        pickup_at=en(-10),
        return_at=en(-2),
        status=ReservationStatus.IN_PROGRESS,
    )

    # El periodo pedido es posterior al de la reserva vencida.
    resultado = check_category_availability(economico, centro, en(1), en(3))

    assert resultado.available, "el motor no sabe que el coche no ha vuelto"


def test_un_borrador_no_reserva_nada(economico, centro, un_coche):
    reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        status=ReservationStatus.DRAFT,
    )

    assert check_category_availability(economico, centro, en(2), en(5)).available


# ---------------------------------------------------------------------------
# 8. Cambio de categoria con el coche ya asignado
# ---------------------------------------------------------------------------


def test_cambiar_de_categoria_deja_el_coche_de_la_categoria_vieja(
    economico, premium, centro, un_coche
):
    """DEFECTO: el motor no suelta ni rechaza el coche que ya no encaja.

    `apply_change` lo libera antes de llamar aqui, pero el servicio de
    disponibilidad es publico y cualquier otro camino se lleva la incoherencia.
    """
    VehicleFactory(plate="7000PRE", category=premium, current_office=centro)
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=un_coche,
        status=ReservationStatus.CONFIRMED,
    )

    cambiada = update_reservation_period(reservation=reserva, category=premium)

    assert cambiada.category == premium
    assert cambiada.vehicle == un_coche
    assert cambiada.vehicle.category == economico, "coche economico en reserva premium"


def test_el_coche_que_ya_no_encaja_sigue_restando_de_su_categoria_vieja(
    economico, premium, centro, un_coche
):
    """El unico coche economico queda ocupado por una reserva premium."""
    VehicleFactory(plate="7001PRE", category=premium, current_office=centro)
    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(2),
        end=en(5),
        vehicle=un_coche,
        status=ReservationStatus.CONFIRMED,
    )
    update_reservation_period(reservation=reserva, category=premium)

    # La reserva ya no es de economico, asi que economico se ve libre...
    assert check_category_availability(economico, centro, en(2), en(5)).available
    # ...pero su unico coche esta fisicamente comprometido.
    with pytest.raises(VehicleNotAvailableError):
        reserve_capacity(
            category=economico,
            pickup_office=centro,
            start=en(2),
            end=en(5),
            vehicle=un_coche,
            status=ReservationStatus.CONFIRMED,
        )


# ---------------------------------------------------------------------------
# 9. Dos empleados asignando el mismo coche a la vez
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_asignar_el_mismo_coche_a_la_vez_revienta_con_integrityerror():
    """DEFECTO: `assign_vehicle` no coge el cerrojo; salta la red de la BD.

    La constraint impide la doble venta (bien), pero el error que sube es un
    IntegrityError crudo, no un `VehicleNotAvailableError`: el mostrador ve un
    500 en lugar de "ese coche lo tiene la reserva X".
    """
    import threading

    from django.db import IntegrityError, connection

    from apps.fleet.tests.factories import VehicleCategoryFactory
    from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory

    pool = OfficePoolFactory(code="ciudad", name="Ciudad")
    centro = OfficeFactory(code="centro", name="Valencia", pool=pool)
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    coche = VehicleFactory(plate="0001AAA", category=categoria, current_office=centro)

    reservas = [
        ReservationFactory(
            category=categoria,
            pickup_office=centro,
            return_office=centro,
            pickup_at=en(1),
            return_at=en(3),
        )
        for _ in range(2)
    ]

    barrera = threading.Barrier(2)
    salidas = []
    cerrojo = threading.Lock()

    def trabajo(reserva):
        try:
            barrera.wait(timeout=15)
            try:
                assign_vehicle(reservation=reserva, vehicle=coche)
                resultado = ("ok", "")
            except VehicleNotAvailableError as exc:
                resultado = ("rechazo_limpio", str(exc))
            except IntegrityError as exc:
                resultado = ("integrityerror", str(exc)[:80])
        finally:
            connection.close()
        with cerrojo:
            salidas.append(resultado)

    hilos = [threading.Thread(target=trabajo, args=(r,)) for r in reservas]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    tipos = sorted(tipo for tipo, _ in salidas)
    assert Reservation.objects.filter(vehicle=coche).count() == 1, "la BD aguanta"
    assert tipos == ["integrityerror", "ok"], f"el perdedor no recibe un error legible: {salidas}"


def test_la_precondicion_de_entrega_no_mira_los_bloqueos(economico, centro, un_coche):
    """DEFECTO: `vehiculo_disponible` solo mira `needs_reassignment`.

    Un bloqueo de taller encima de la fecha de entrega no la hace saltar, y
    nada mas en la maquina de estados vuelve a preguntar por el coche.
    """
    from apps.reservations.state_machine import vehiculo_disponible

    reserva = reserve_capacity(
        category=economico,
        pickup_office=centro,
        start=en(0.1),
        end=en(3),
        vehicle=un_coche,
        status=ReservationStatus.CONFIRMED,
    )
    VehicleBlockFactory(vehicle=un_coche, start_at=en(0), end_at=en(4))

    reserva.refresh_from_db()
    assert reserva.vehicle.blocks.exists()
    assert vehiculo_disponible(reserva) is None, "el taller no impide la entrega"
