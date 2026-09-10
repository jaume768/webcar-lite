"""La maquina de estados: que saltos existen y quien puede darlos."""

from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.tests.factories import UserFactory
from apps.reservations.models import (
    CancellationPolicy,
    Reservation,
    ReservationStatus,
    ReservationStatusChange,
)
from apps.reservations.services import record_pickup, record_return
from apps.reservations.state_machine import (
    TRANSITIONS,
    InvalidTransition,
    TransitionRefused,
    available_transitions,
    can_transition,
    transition,
)

from .factories import ReservationFactory, en

pytestmark = pytest.mark.django_db

TODOS_LOS_ESTADOS = list(ReservationStatus.values)


@pytest.fixture
def jefe(db):
    """Superusuario: los permisos nunca tapan un salto invalido."""
    return UserFactory(email="jefe@ejemplo.es", is_superuser=True, is_staff=True)


def _reserva(economico, palma, cliente, **kwargs):
    kwargs.setdefault("category", economico)
    kwargs.setdefault("pickup_office", palma)
    kwargs.setdefault("return_office", palma)
    kwargs.setdefault("customer", cliente)
    return ReservationFactory(**kwargs)


# ---------------------------------------------------------------------------
# El diagrama entero
# ---------------------------------------------------------------------------

SALTOS_VALIDOS = {(desde, hasta) for desde, destinos in TRANSITIONS.items() for hasta in destinos}
SALTOS_INVALIDOS = [
    (desde, hasta)
    for desde in TODOS_LOS_ESTADOS
    for hasta in TODOS_LOS_ESTADOS
    if (desde, hasta) not in SALTOS_VALIDOS
]


def test_el_diagrama_cubre_todos_los_estados():
    """Un estado que no aparezca en TRANSITIONS seria un callejon silencioso."""
    assert set(TRANSITIONS) == set(TODOS_LOS_ESTADOS)


@pytest.mark.parametrize(("desde", "hasta"), SALTOS_INVALIDOS)
def test_todo_salto_fuera_del_diagrama_falla(economico, palma, cliente, jefe, desde, hasta):
    reserva = _reserva(economico, palma, cliente, status=desde)

    with pytest.raises(InvalidTransition):
        transition(reserva, hasta, jefe)

    reserva.refresh_from_db()
    assert reserva.status == desde
    assert not ReservationStatusChange.objects.filter(reservation=reserva).exists()


def test_no_se_puede_finalizar_saltandose_en_curso(economico, palma, cliente, jefe, coche):
    """El caso que mas duele: confirmar y dar por terminado sin entregar."""
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED, vehicle=coche)

    with pytest.raises(InvalidTransition) as fallo:
        transition(reserva, ReservationStatus.FINISHED, jefe)

    assert "Confirmada" in str(fallo.value)
    reserva.refresh_from_db()
    assert reserva.status == ReservationStatus.CONFIRMED


def test_un_estado_final_no_va_a_ninguna_parte(economico, palma, cliente, jefe):
    for final in (
        ReservationStatus.FINISHED,
        ReservationStatus.CANCELLED,
        ReservationStatus.NO_SHOW,
    ):
        assert TRANSITIONS[final] == {}


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------


def test_una_transicion_sin_permiso_no_deja_rastro(economico, palma, cliente, solo_lectura):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.PENDING)

    with pytest.raises(PermissionDenied):
        transition(reserva, ReservationStatus.CONFIRMED, solo_lectura)

    reserva.refresh_from_db()
    assert reserva.status == ReservationStatus.PENDING
    assert ReservationStatusChange.objects.count() == 0


def test_el_agente_confirma_pero_no_cancela(economico, palma, cliente, agente):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.PENDING)

    confirmada = transition(reserva, ReservationStatus.CONFIRMED, agente)
    assert confirmada.status == ReservationStatus.CONFIRMED

    with pytest.raises(PermissionDenied):
        transition(confirmada, ReservationStatus.CANCELLED, agente)


def test_available_transitions_depende_del_usuario(economico, palma, cliente, agente, responsable):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED)

    del_agente = {t.to for t in available_transitions(reserva, agente)}
    del_responsable = {t.to for t in available_transitions(reserva, responsable)}

    assert ReservationStatus.CANCELLED not in del_agente
    assert ReservationStatus.CANCELLED in del_responsable
    assert ReservationStatus.IN_PROGRESS in del_agente


# ---------------------------------------------------------------------------
# Precondiciones
# ---------------------------------------------------------------------------


def test_confirmar_sin_cliente_se_rechaza(economico, palma, agente):
    reserva = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=None,
        status=ReservationStatus.PENDING,
    )

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.CONFIRMED, agente)

    assert "cliente" in str(fallo.value)


def test_entregar_sin_vehiculo_se_rechaza(economico, palma, cliente, agente):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED)

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.IN_PROGRESS, agente)

    assert "vehiculo" in str(fallo.value)


def test_entregar_sin_check_in_se_rechaza(economico, palma, cliente, agente, coche):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED, vehicle=coche)

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.IN_PROGRESS, agente)

    assert "check-in" in str(fallo.value)


def test_entregar_con_todo_en_orden(economico, palma, cliente, agente, coche):
    from apps.fleet.models import VehicleStatus

    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED, vehicle=coche)
    record_pickup(reservation=reserva, actor=agente)

    en_curso = transition(reserva, ReservationStatus.IN_PROGRESS, agente)

    assert en_curso.status == ReservationStatus.IN_PROGRESS
    coche.refresh_from_db()
    assert coche.status == VehicleStatus.RENTED


def test_finalizar_sin_check_out_se_rechaza(economico, palma, cliente, agente, coche):
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.IN_PROGRESS,
        vehicle=coche,
        actual_pickup_at=en(0),
    )

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.FINISHED, agente)

    assert "check-out" in str(fallo.value)


def test_finalizar_manda_el_coche_a_limpieza(economico, palma, cliente, agente, coche):
    """A limpieza y no a disponible: entre que entra y vuelve a salir hay trabajo."""
    from apps.fleet.models import VehicleStatus
    from apps.fleet.services import start_rental

    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.IN_PROGRESS,
        vehicle=coche,
        actual_pickup_at=en(0),
    )
    start_rental(vehicle=coche, actor=agente)
    record_return(reservation=reserva, actor=agente)

    finalizada = transition(reserva, ReservationStatus.FINISHED, agente)

    assert finalizada.status == ReservationStatus.FINISHED
    coche.refresh_from_db()
    assert coche.status == VehicleStatus.CLEANING


def test_no_se_entrega_un_coche_que_salio_de_flota(economico, palma, cliente, agente, coche):
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.CONFIRMED,
        vehicle=coche,
        actual_pickup_at=en(0),
        needs_reassignment=True,
    )

    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.IN_PROGRESS, agente)

    assert "salio de flota" in str(fallo.value)


# ---------------------------------------------------------------------------
# Cancelacion
# ---------------------------------------------------------------------------


def test_cancelar_una_reserva_en_curso_exige_permiso_y_motivo(
    economico, palma, cliente, agente, responsable, coche
):
    """El coche esta fuera: no es una cancelacion cualquiera."""
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.IN_PROGRESS,
        vehicle=coche,
        actual_pickup_at=en(0),
    )

    # Sin permiso, ni con motivo.
    with pytest.raises(PermissionDenied):
        transition(reserva, ReservationStatus.CANCELLED, agente, reason="da igual")

    # Con permiso pero sin motivo, tampoco.
    with pytest.raises(TransitionRefused) as fallo:
        transition(reserva, ReservationStatus.CANCELLED, responsable)
    assert "motivo" in str(fallo.value)

    with pytest.raises(TransitionRefused):
        transition(reserva, ReservationStatus.CANCELLED, responsable, reason="   ")

    reserva.refresh_from_db()
    assert reserva.status == ReservationStatus.IN_PROGRESS
    assert ReservationStatusChange.objects.count() == 0

    # Con las dos cosas, sale.
    cancelada = transition(
        reserva,
        ReservationStatus.CANCELLED,
        responsable,
        reason="Averia grave; el cliente se queda sin coche.",
    )

    assert cancelada.status == ReservationStatus.CANCELLED
    cambio = ReservationStatusChange.objects.get()
    assert cambio.changed_by == responsable
    assert "Averia" in cambio.reason


def test_cancelar_una_pendiente_no_pide_motivo(economico, palma, cliente, responsable):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.PENDING)

    cancelada = transition(reserva, ReservationStatus.CANCELLED, responsable)

    assert cancelada.status == ReservationStatus.CANCELLED


def test_el_no_show_exige_motivo(economico, palma, cliente, responsable):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED)

    with pytest.raises(TransitionRefused):
        transition(reserva, ReservationStatus.NO_SHOW, responsable)

    marcada = transition(
        reserva, ReservationStatus.NO_SHOW, responsable, reason="No aparecio en 3 horas."
    )
    assert marcada.status == ReservationStatus.NO_SHOW


def test_cancelar_aplica_la_politica_y_congela_el_cargo(economico, palma, cliente, responsable):
    """Cancelar con el coche a punto de salir cuesta un dia."""
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.CONFIRMED,
        pickup_at=en(0.2),
        return_at=en(3),
        cancellation_policy=CancellationPolicy.FLEXIBLE,
        base_amount=Decimal("120.00"),
        price_breakdown={"rental_days": 3},
    )

    cancelada = transition(reserva, ReservationStatus.CANCELLED, responsable)

    assert cancelada.cancellation_fee == Decimal("40.00")


def test_cancelar_con_antelacion_no_cuesta_nada(economico, palma, cliente, responsable):
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.CONFIRMED,
        pickup_at=en(10),
        return_at=en(13),
        cancellation_policy=CancellationPolicy.FLEXIBLE,
    )

    cancelada = transition(reserva, ReservationStatus.CANCELLED, responsable)

    assert cancelada.cancellation_fee == 0


def test_una_no_reembolsable_cuesta_el_total(economico, palma, cliente, responsable):
    reserva = _reserva(
        economico,
        palma,
        cliente,
        status=ReservationStatus.CONFIRMED,
        pickup_at=en(30),
        return_at=en(33),
        cancellation_policy=CancellationPolicy.NON_REFUNDABLE,
        total=Decimal("145.20"),
    )

    cancelada = transition(reserva, ReservationStatus.CANCELLED, responsable)

    assert cancelada.cancellation_fee == reserva.total


# ---------------------------------------------------------------------------
# Historico
# ---------------------------------------------------------------------------


def test_cada_transicion_deja_su_fila(economico, palma, cliente, agente, responsable):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.DRAFT)

    transition(reserva, ReservationStatus.PENDING, agente)
    transition(reserva, ReservationStatus.CONFIRMED, agente)
    transition(reserva, ReservationStatus.CANCELLED, responsable, reason="Cambio de planes")

    cambios = list(
        ReservationStatusChange.objects.filter(reservation=reserva).order_by("created_at", "id")
    )
    assert [(c.from_status, c.to_status) for c in cambios] == [
        (ReservationStatus.DRAFT, ReservationStatus.PENDING),
        (ReservationStatus.PENDING, ReservationStatus.CONFIRMED),
        (ReservationStatus.CONFIRMED, ReservationStatus.CANCELLED),
    ]
    assert cambios[-1].reason == "Cambio de planes"
    assert cambios[0].changed_by == agente


def test_can_transition_no_miente(economico, palma, cliente, agente):
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.CONFIRMED)

    # Sin vehiculo, la precondicion no se cumple.
    assert not can_transition(reserva, ReservationStatus.IN_PROGRESS, agente)
    assert not can_transition(reserva, ReservationStatus.FINISHED, agente)


def test_el_estado_se_relee_con_bloqueo(economico, palma, cliente, agente):
    """Si otro movio la reserva antes del POST, el salto ya no es el que era."""
    reserva = _reserva(economico, palma, cliente, status=ReservationStatus.PENDING)
    Reservation.objects.filter(pk=reserva.pk).update(status=ReservationStatus.CANCELLED)

    with pytest.raises(InvalidTransition):
        transition(reserva, ReservationStatus.CONFIRMED, agente)
