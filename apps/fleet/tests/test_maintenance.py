"""Mantenimiento y taller: revisiones, ITV, averias e inmovilizaciones."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.availability.services import check_category_availability
from apps.fleet.maintenance import (
    MaintenanceError,
    cancel_record,
    finish_workshop,
    start_workshop,
    upcoming,
)
from apps.fleet.models import (
    BlockReason,
    MaintenanceKind,
    MaintenanceRecord,
    MaintenanceStatus,
    VehicleBlock,
    VehicleStatus,
)
from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
from apps.offices.tests.factories import OfficeFactory
from apps.reservations.models import ReservationStatus
from apps.reservations.tests.factories import ReservationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def oficina(db):
    return OfficeFactory(code="taller-centro", name="Centro")


@pytest.fixture
def categoria(db):
    return VehicleCategoryFactory(code="eco-taller")


@pytest.fixture
def coche(oficina, categoria):
    return VehicleFactory(
        plate="9999TLL", category=categoria, current_office=oficina, mileage=40000
    )


def _registro(coche, **kwargs):
    kwargs.setdefault("kind", MaintenanceKind.BREAKDOWN)
    return MaintenanceRecord.objects.create(vehicle=coche, **kwargs)


def test_entrar_en_taller_bloquea_el_calendario_y_el_coche(coche, categoria, oficina):
    ahora = timezone.now()
    registro = _registro(coche)

    start_workshop(record=registro, started_at=ahora, expected_end_at=ahora + timedelta(days=2))

    registro.refresh_from_db()
    coche.refresh_from_db()
    assert registro.status == MaintenanceStatus.IN_WORKSHOP
    assert registro.block.reason == BlockReason.WORKSHOP
    assert coche.status == VehicleStatus.WORKSHOP
    # La disponibilidad lo resta como cualquier bloqueo.
    resultado = check_category_availability(
        categoria, oficina, ahora + timedelta(hours=1), ahora + timedelta(days=1)
    )
    assert resultado.free == 0


def test_la_reserva_que_tenia_el_coche_lo_suelta_sin_cancelarse(coche, categoria, oficina):
    ahora = timezone.now()
    reserva = ReservationFactory(
        category=categoria,
        vehicle=coche,
        pickup_office=oficina,
        return_office=oficina,
        pickup_at=ahora + timedelta(days=1),
        return_at=ahora + timedelta(days=3),
        status=ReservationStatus.CONFIRMED,
    )
    registro = _registro(coche)

    afectadas = start_workshop(
        record=registro, started_at=ahora, expected_end_at=ahora + timedelta(days=2)
    )

    reserva.refresh_from_db()
    assert afectadas == [reserva]
    assert reserva.vehicle is None
    assert reserva.needs_reassignment
    assert reserva.status == ReservationStatus.CONFIRMED


def test_revision_sin_inmovilizar_no_bloquea(coche):
    registro = _registro(coche, kind=MaintenanceKind.OIL, immobilizes=False)

    start_workshop(record=registro)

    coche.refresh_from_db()
    assert not VehicleBlock.objects.exists()
    assert coche.status == VehicleStatus.AVAILABLE


def test_un_coche_alquilado_no_entra_en_taller(coche):
    coche.status = VehicleStatus.RENTED
    coche.save(update_fields=["status"])
    with pytest.raises(MaintenanceError):
        start_workshop(record=_registro(coche))


def test_salir_del_taller_devuelve_el_coche_y_apunta_la_itv(coche):
    registro = _registro(coche, kind=MaintenanceKind.ITV)
    start_workshop(record=registro)
    nueva_itv = date.today() + timedelta(days=365)

    finish_workshop(record=registro, mileage=41000, cost=Decimal("45.50"), next_due_date=nueva_itv)

    registro.refresh_from_db()
    coche.refresh_from_db()
    assert registro.status == MaintenanceStatus.DONE
    assert registro.block is None
    assert not VehicleBlock.objects.exists()
    assert coche.status == VehicleStatus.AVAILABLE
    assert coche.mileage == 41000
    assert coche.itv_expiry == nueva_itv


def test_los_kilometros_no_van_hacia_atras(coche):
    registro = _registro(coche, kind=MaintenanceKind.TYRES, immobilizes=False)
    finish_workshop(record=registro, mileage=100)
    coche.refresh_from_db()
    assert coche.mileage == 40000


def test_anular_quita_el_bloqueo(coche):
    registro = _registro(coche)
    start_workshop(record=registro)

    cancel_record(record=registro)

    coche.refresh_from_db()
    assert not VehicleBlock.objects.exists()
    assert coche.status == VehicleStatus.AVAILABLE


def test_lo_hecho_no_se_anula(coche):
    registro = _registro(coche, immobilizes=False)
    finish_workshop(record=registro)
    with pytest.raises(MaintenanceError):
        cancel_record(record=registro)


def test_avisos_de_vencimiento_por_fecha_km_e_itv(coche, superusuario):
    hoy = date.today()
    finish_workshop(record=_registro(coche, kind=MaintenanceKind.OIL), next_due_km=40500)
    finish_workshop(
        record=_registro(coche, kind=MaintenanceKind.SERVICE), next_due_date=hoy + timedelta(days=5)
    )
    coche.refresh_from_db()
    coche.itv_expiry = hoy - timedelta(days=1)
    coche.save(update_fields=["itv_expiry"])

    avisos = upcoming(superusuario, today=hoy)

    etiquetas = {str(aviso.label): aviso for aviso in avisos}
    assert etiquetas["ITV"].overdue
    assert not etiquetas["Aceite y filtros"].overdue
    assert etiquetas["Aceite y filtros"].km_left == 500
    assert etiquetas["Revisión"].due_date == hoy + timedelta(days=5)
    # Lo vencido va primero.
    assert avisos[0].overdue
