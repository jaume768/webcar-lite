"""Alta rapida de mostrador: precio congelado, numeracion y disponibilidad."""

import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.availability.services import NoAvailabilityError
from apps.pricing.dto import ExtraRequest
from apps.pricing.models import CalculationType
from apps.reservations.models import (
    Reservation,
    ReservationCounter,
    ReservationExtra,
    ReservationStatus,
    ReservationStatusChange,
)
from apps.reservations.services import ReservationServiceError, create_quick_reservation

from .factories import ExtraFactory, en

pytestmark = pytest.mark.django_db


def _alta(economico, palma, cliente, **kwargs):
    kwargs.setdefault("pickup_at", en(1))
    kwargs.setdefault("return_at", en(4))
    return create_quick_reservation(
        category=economico, pickup_office=palma, customer=cliente, **kwargs
    )


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


def test_el_alta_deja_la_reserva_pendiente_y_con_precio(
    economico, palma, cliente, coche, tarifa, agente
):
    reserva = _alta(economico, palma, cliente, actor=agente)

    assert reserva.status == ReservationStatus.PENDING
    assert reserva.customer == cliente
    assert reserva.rate == tarifa
    assert reserva.total > 0
    assert reserva.base_amount > 0
    # 3 dias al tramo 2-3 (45 EUR/dia).
    assert reserva.base_amount == Decimal("135.00")


def test_el_desglose_se_guarda_entero(economico, palma, cliente, coche, tarifa, agente):
    reserva = _alta(economico, palma, cliente, actor=agente)

    desglose = reserva.price_breakdown
    assert desglose["rental_days"] == 3
    assert desglose["applied_rate"] == tarifa.code
    assert [linea["kind"] for linea in desglose["lines"]] == ["rental"]
    # Los importes viajan como cadena: ni un float en el JSON.
    assert isinstance(desglose["total"], str)


def test_el_alta_deja_rastro_en_el_historico(economico, palma, cliente, coche, tarifa, agente):
    reserva = _alta(economico, palma, cliente, actor=agente)

    cambio = ReservationStatusChange.objects.get(reservation=reserva)
    assert cambio.from_status == ReservationStatus.DRAFT
    assert cambio.to_status == ReservationStatus.PENDING
    assert cambio.changed_by == agente


def test_sin_cliente_no_hay_alta(economico, palma, coche, tarifa, agente):
    with pytest.raises(ReservationServiceError):
        create_quick_reservation(
            category=economico,
            pickup_office=palma,
            customer=None,
            pickup_at=en(1),
            return_at=en(4),
            actor=agente,
        )


def test_un_cliente_marcado_no_alquila(economico, palma, cliente, coche, tarifa, agente):
    cliente.is_blacklisted = True
    cliente.blacklist_reason = "Devolvio el coche con danos sin declarar."
    cliente.save(update_fields=["is_blacklisted", "blacklist_reason"])

    with pytest.raises(ReservationServiceError) as fallo:
        _alta(economico, palma, cliente, actor=agente)

    assert "marcado" in str(fallo.value)


# ---------------------------------------------------------------------------
# Disponibilidad
# ---------------------------------------------------------------------------


def test_sin_disponibilidad_no_se_escribe_nada(economico, palma, cliente, coche, tarifa, agente):
    """La comprobacion va dentro de la transaccion: o entra todo, o nada."""
    _alta(economico, palma, cliente, actor=agente)  # el unico coche queda cogido
    reservas_antes = Reservation.objects.count()
    numero_antes = ReservationCounter.objects.get().last_number

    with pytest.raises(NoAvailabilityError):
        _alta(economico, palma, cliente, actor=agente)

    assert Reservation.objects.count() == reservas_antes
    assert ReservationExtra.objects.count() == 0
    # Y el numero tampoco se ha gastado.
    assert ReservationCounter.objects.get().last_number == numero_antes


def test_sin_flota_tampoco(economico, palma, cliente, tarifa, agente):
    with pytest.raises(NoAvailabilityError):
        _alta(economico, palma, cliente, actor=agente)

    assert not Reservation.objects.exists()


# ---------------------------------------------------------------------------
# Extras congelados
# ---------------------------------------------------------------------------


@pytest.fixture
def silla(db):
    return ExtraFactory(
        code="silla",
        name="Silla infantil",
        price=Decimal("5.00"),
        tax_rate=Decimal("21.00"),
        calculation_type=CalculationType.PER_DAY,
    )


def test_los_extras_se_guardan_con_su_precio(
    economico, palma, cliente, coche, tarifa, agente, silla
):
    reserva = _alta(
        economico, palma, cliente, actor=agente, extras=[ExtraRequest(extra=silla, quantity=1)]
    )

    linea = ReservationExtra.objects.get(reservation=reserva)
    # El motor factura el extra como una linea del periodo, no una por dia:
    # 5 EUR/dia x 3 dias = 15 EUR la unidad.
    assert linea.concept == "Silla infantil (3 dias)"
    assert linea.unit_price == Decimal("15.00")
    assert linea.tax_rate == Decimal("21.00")
    assert linea.base_amount == Decimal("15.00")
    assert linea.total == linea.base_amount + linea.tax_amount
    # extras_total es base imponible; el IVA de la linea esta en tax_total.
    assert reserva.extras_total == linea.base_amount
    assert reserva.total == reserva.base_amount + reserva.extras_total + reserva.tax_total


def test_cambiar_el_maestro_no_toca_lo_ya_vendido(
    economico, palma, cliente, coche, tarifa, agente, silla
):
    """Sube la silla infantil: la reserva de ayer sigue diciendo lo que se cobro."""
    reserva = _alta(
        economico, palma, cliente, actor=agente, extras=[ExtraRequest(extra=silla, quantity=1)]
    )
    linea_antes = ReservationExtra.objects.get(reservation=reserva)
    total_antes = reserva.total

    silla.price = Decimal("25.00")
    silla.name = "Silla infantil premium"
    silla.save(update_fields=["price", "name"])

    linea_antes.refresh_from_db()
    reserva.refresh_from_db()

    assert linea_antes.unit_price == Decimal("15.00")
    assert linea_antes.concept == "Silla infantil (3 dias)"
    assert reserva.total == total_antes
    assert reserva.price_breakdown["lines"][1]["unit_price"] == "15.00"


def test_retirar_el_extra_del_catalogo_no_borra_la_linea(
    economico, palma, cliente, coche, tarifa, agente, silla
):
    reserva = _alta(
        economico, palma, cliente, actor=agente, extras=[ExtraRequest(extra=silla, quantity=1)]
    )

    silla.deactivate()

    assert ReservationExtra.objects.filter(reservation=reserva).count() == 1


# ---------------------------------------------------------------------------
# Numeracion
# ---------------------------------------------------------------------------


def test_los_numeros_son_correlativos(economico, palma, cliente, tarifa, agente):
    from apps.fleet.tests.factories import VehicleFactory

    for i in range(3):
        VehicleFactory(plate=f"800{i}NUM", category=economico, current_office=palma)

    numeros = [_alta(economico, palma, cliente, actor=agente).number for _ in range(3)]

    assert numeros == sorted(numeros)
    assert len(set(numeros)) == 3
    assert numeros[0].endswith("00001")
    assert numeros[-1].endswith("00003")


def test_el_formato_del_numero_es_configurable(
    economico, palma, cliente, coche, tarifa, agente, settings
):
    settings.RESERVATION_NUMBER_FORMAT = "RES/{sequence:04d}"

    reserva = _alta(economico, palma, cliente, actor=agente)

    assert reserva.number == "RES/0001"


@pytest.mark.django_db(transaction=True)
def test_altas_simultaneas_numeros_distintos_y_sin_huecos():
    """Dos empleados dando de alta a la vez no pueden repetir numero."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory
    from apps.customers.tests.factories import CustomerFactory
    from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
    from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
    from apps.pricing.tests.factories import TRAMOS_ESTANDAR, RateFactory

    pool = OfficePoolFactory(code="baleares", name="Baleares")
    oficina = OfficeFactory(code="palma", name="Palma", pool=pool)
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    RateFactory(
        code="mostrador",
        name="Mostrador",
        categories=[categoria],
        offices=[oficina],
        tiers=TRAMOS_ESTANDAR,
    )
    rol = RoleFactory(code="mostrador-num", name="Mostrador")
    usuario = UserFactory(email="num@ejemplo.es", role=rol, offices=[oficina])
    clientes = [CustomerFactory() for _ in range(6)]
    for i in range(6):
        VehicleFactory(plate=f"90{i:02d}NUM", category=categoria, current_office=oficina)

    barrera = threading.Barrier(6)
    resultados = []
    cerrojo = threading.Lock()

    def alta(cliente):
        try:
            barrera.wait(timeout=15)
            try:
                reserva = create_quick_reservation(
                    category=categoria,
                    pickup_office=oficina,
                    customer=cliente,
                    pickup_at=en(1),
                    return_at=en(4),
                    actor=usuario,
                )
                salida = ("ok", reserva.number)
            except Exception as exc:
                salida = ("error", f"{type(exc).__name__}: {exc}")
        finally:
            connection.close()
        with cerrojo:
            resultados.append(salida)

    hilos = [threading.Thread(target=alta, args=(c,)) for c in clientes]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    errores = [r for r in resultados if r[0] == "error"]
    numeros = sorted(r[1] for r in resultados if r[0] == "ok")

    assert not errores, errores
    assert len(numeros) == 6
    assert len(set(numeros)) == 6, f"numero repetido: {numeros}"

    # Correlativos y sin saltos.
    secuencias = sorted(int(n.split("-")[-1]) for n in numeros)
    assert secuencias == list(range(1, 7)), f"la serie tiene huecos: {secuencias}"
    assert ReservationCounter.objects.get().last_number == 6
