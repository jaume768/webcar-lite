"""Planning: geometria de las barras, carriles, scope de oficina y pantalla."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.fleet.tests.factories import VehicleFactory
from apps.reservations.models import ReservationStatus
from apps.reservations.planning import _barras, build_planning, normalize_length

from .factories import ReservationFactory

LUNES = date(2026, 9, 21)


def _en(dia: date, hora: int = 10):
    return timezone.make_aware(datetime.combine(dia, datetime.min.time()).replace(hour=hora))


def _reserva(desde: date, hasta: date, pk: int = 1):
    """Lo minimo que mira `_barras`: sin base de datos."""
    return SimpleNamespace(pk=pk, pickup_at=_en(desde), return_at=_en(hasta))


# ---------------------------------------------------------------------------
# Geometria, sin base de datos
# ---------------------------------------------------------------------------


def test_una_reserva_ocupa_de_su_dia_de_recogida_al_de_devolucion():
    (barra,) = _barras([_reserva(LUNES + timedelta(days=2), LUNES + timedelta(days=4))], LUNES, 7)

    assert (barra.start_col, barra.span, barra.lane) == (3, 3, 1)
    assert not barra.cut_start and not barra.cut_end


def test_lo_que_se_sale_del_rango_se_recorta_y_se_marca():
    (barra,) = _barras([_reserva(LUNES - timedelta(days=3), LUNES + timedelta(days=20))], LUNES, 7)

    assert (barra.start_col, barra.span) == (1, 7)
    assert barra.cut_start and barra.cut_end


def test_lo_que_queda_fuera_del_rango_no_pinta_barra():
    fuera = _reserva(LUNES + timedelta(days=10), LUNES + timedelta(days=12))

    assert _barras([fuera], LUNES, 7) == []


def test_las_que_se_solapan_van_en_carriles_distintos():
    barras = _barras(
        [
            _reserva(LUNES, LUNES + timedelta(days=2), pk=1),
            _reserva(LUNES + timedelta(days=1), LUNES + timedelta(days=3), pk=2),
            _reserva(LUNES + timedelta(days=3), LUNES + timedelta(days=4), pk=3),
        ],
        LUNES,
        7,
    )
    carril = {barra.reservation.pk: barra.lane for barra in barras}

    # La tercera empieza cuando la primera ya ha terminado: reaprovecha su carril.
    assert carril == {1: 1, 2: 2, 3: 1}


@pytest.mark.parametrize(("valor", "esperado"), [("7", 7), (31, 31), ("99", 14), (None, 14)])
def test_solo_se_admiten_las_duraciones_del_selector(valor, esperado):
    assert normalize_length(valor) == esperado


# ---------------------------------------------------------------------------
# Con base de datos
# ---------------------------------------------------------------------------


@pytest.fixture
def de_aeropuerto(db, aeropuerto):
    return UserFactory(
        email="aeropuerto@ejemplo.es",
        role=RoleFactory(
            code="ver-reservas", name="Ver", permissions=["reservations.view_reservation"]
        ),
        offices=[aeropuerto],
    )


@pytest.fixture
def escenario(db, centro, economico, coche, cliente):
    hoy = timezone.localdate()
    asignada = ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
        customer=cliente,
        vehicle=coche,
        pickup_at=_en(hoy + timedelta(days=1)),
        return_at=_en(hoy + timedelta(days=3)),
    )
    sin_coche = ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
        customer=cliente,
        vehicle=None,
        pickup_at=_en(hoy + timedelta(days=2)),
        return_at=_en(hoy + timedelta(days=4)),
    )
    ReservationFactory(
        category=economico,
        pickup_office=centro,
        return_office=centro,
        customer=cliente,
        vehicle=None,
        status=ReservationStatus.CANCELLED,
        pickup_at=_en(hoy + timedelta(days=2)),
        return_at=_en(hoy + timedelta(days=4)),
    )
    return asignada, sin_coche


def test_una_fila_por_coche_y_otra_para_lo_que_no_tiene(agente, escenario, economico, centro):
    asignada, sin_coche = escenario
    VehicleFactory(plate="9999ZZZ", category=economico, current_office=centro)

    planning = build_planning(user=agente, start=timezone.localdate(), length=7)

    (grupo,) = planning.groups
    filas = {fila.title: fila for fila in grupo.rows}
    assert grupo.category == economico
    assert [b.reservation for b in filas["1234ABC"].bars] == [asignada]
    assert filas["9999ZZZ"].bars == []
    assert [b.reservation for b in filas["Sin asignar"].bars] == [sin_coche]
    # La cancelada no ocupa nada.
    assert planning.total == 2


def test_otra_oficina_no_ve_nada(de_aeropuerto, escenario):
    planning = build_planning(user=de_aeropuerto, start=timezone.localdate(), length=7)

    assert planning.groups == []


def test_la_pantalla_enlaza_cada_barra_con_su_reserva(client, agente, escenario):
    asignada, _sin_coche = escenario
    client.force_login(agente)

    contenido = client.get(reverse("reservations:planning"), {"dias": 7}).content.decode()

    assert reverse("reservations:detail", args=[asignada.pk]) in contenido
    assert asignada.number in contenido


def test_una_oficina_ajena_por_la_url_no_cuela(client, agente, aeropuerto, escenario):
    """El formulario no la valida y el planning se queda en las del usuario."""
    client.force_login(agente)

    respuesta = client.get(reverse("reservations:planning"), {"office": aeropuerto.pk})

    assert respuesta.status_code == 200
    assert escenario[0].number in respuesta.content.decode()


def test_sin_permiso_no_hay_planning(client, db, centro):
    sin_permiso = UserFactory(email="nadie@ejemplo.es", offices=[centro])
    client.force_login(sin_permiso)

    assert client.get(reverse("reservations:planning")).status_code == 403
