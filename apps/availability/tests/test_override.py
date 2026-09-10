"""Overbooking consciente: permiso, motivo obligatorio y rastro."""

import pytest

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.availability.services import (
    AvailabilityError,
    NoAvailabilityError,
    Override,
    reserve_capacity,
)

from .factories import en

pytestmark = pytest.mark.django_db

MOTIVO = "Cliente VIP; entra un coche de Alcudia esa manana."


@pytest.fixture
def responsable(db, palma):
    rol = RoleFactory(
        code="responsable-test",
        name="Responsable",
        permissions=["availability.override_availability"],
    )
    return UserFactory(email="responsable@ejemplo.es", role=rol, offices=[palma])


@pytest.fixture
def agente(db, palma):
    rol = RoleFactory(code="mostrador-test", name="Mostrador")
    return UserFactory(email="agente@ejemplo.es", role=rol, offices=[palma])


@pytest.fixture
def sin_hueco(economico, palma, un_coche):
    """La categoria queda al limite: el siguiente que pida, no cabe."""
    reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))
    return economico


def test_sin_permiso_no_se_puede_forzar(sin_hueco, palma, agente):
    with pytest.raises(AvailabilityError) as fallo:
        reserve_capacity(
            category=sin_hueco,
            pickup_office=palma,
            start=en(1),
            end=en(3),
            override=Override(user=agente, reason=MOTIVO),
        )

    assert "no puede forzar" in str(fallo.value)


def test_con_permiso_pero_sin_motivo_tampoco(sin_hueco, palma, responsable):
    """El motivo no es un adorno: es lo que explica la decision manana."""
    for motivo in ("", "   "):
        with pytest.raises(AvailabilityError) as fallo:
            reserve_capacity(
                category=sin_hueco,
                pickup_office=palma,
                start=en(1),
                end=en(3),
                override=Override(user=responsable, reason=motivo),
            )
        assert "motivo" in str(fallo.value)


def test_con_permiso_y_motivo_se_acepta_y_queda_registrado(sin_hueco, palma, responsable):
    reserva = reserve_capacity(
        category=sin_hueco,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        override=Override(user=responsable, reason=MOTIVO),
    )

    reserva.refresh_from_db()
    assert reserva.overbooked
    assert reserva.override_reason == MOTIVO
    assert reserva.override_by == responsable


def test_sin_override_la_misma_reserva_se_rechaza(sin_hueco, palma):
    with pytest.raises(NoAvailabilityError):
        reserve_capacity(category=sin_hueco, pickup_office=palma, start=en(1), end=en(3))


def test_una_reserva_normal_no_queda_marcada(economico, palma, un_coche):
    reserva = reserve_capacity(category=economico, pickup_office=palma, start=en(1), end=en(3))

    assert not reserva.overbooked
    assert reserva.override_reason == ""
    assert reserva.override_by is None


def test_el_overbooking_no_desactiva_la_constraint_del_vehiculo(
    sin_hueco, palma, un_coche, responsable
):
    """Se puede vender de mas, pero no dar el mismo coche a dos clientes."""
    from apps.availability.services import VehicleNotAvailableError, assign_vehicle

    primera = sin_hueco.reservations.first()
    assign_vehicle(reservation=primera, vehicle=un_coche)

    forzada = reserve_capacity(
        category=sin_hueco,
        pickup_office=palma,
        start=en(1),
        end=en(3),
        override=Override(user=responsable, reason=MOTIVO),
    )

    with pytest.raises(VehicleNotAvailableError):
        assign_vehicle(reservation=forzada, vehicle=un_coche)
