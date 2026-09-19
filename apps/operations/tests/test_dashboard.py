"""Panel de mostrador: scope, consultas acotadas y contenido."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.availability.services import assign_vehicle
from apps.billing.models import PaymentMethod, PaymentType
from apps.billing.services import register_payment
from apps.customers.tests.factories import CustomerFactory
from apps.fleet.models import VehicleStatus
from apps.fleet.tests.factories import VehicleFactory
from apps.operations.dashboard import PERIODS, build_dashboard, period_for
from apps.reservations.models import ReservationStatus
from apps.reservations.services import create_quick_reservation
from apps.reservations.tests.factories import ReservationFactory

pytestmark = pytest.mark.django_db


def _hoy_a_las(hora: int):
    """Instante de hoy a esa hora local, para que caiga en el dia natural."""
    ahora = timezone.localtime()
    return ahora.replace(hour=hora, minute=0, second=0, microsecond=0)


@pytest.fixture
def rol_mostrador(db):
    return RoleFactory(
        code="mostrador-panel",
        name="Mostrador",
        permissions=[
            "reservations.view_reservation",
            "reservations.add_reservation",
            "reservations.change_reservation",
        ],
    )


@pytest.fixture
def de_palma(db, palma, rol_mostrador):
    """Empleado con una sola oficina."""
    return UserFactory(email="palma@ejemplo.es", role=rol_mostrador, offices=[palma])


@pytest.fixture
def de_alcudia(db, aeropuerto, rol_mostrador):
    return UserFactory(email="alcudia@ejemplo.es", role=rol_mostrador, offices=[aeropuerto])


@pytest.fixture
def de_las_dos(db, palma, aeropuerto, rol_mostrador):
    return UserFactory(email="ambas@ejemplo.es", role=rol_mostrador, offices=[palma, aeropuerto])


def _reserva_de_hoy(categoria, oficina, *, hora=10, vehiculo=None, status=None):
    reserva = ReservationFactory(
        category=categoria,
        pickup_office=oficina,
        return_office=oficina,
        customer=CustomerFactory(),
        pickup_at=_hoy_a_las(hora),
        return_at=_hoy_a_las(hora) + timedelta(days=3),
        vehicle=vehiculo,
        status=status or ReservationStatus.CONFIRMED,
    )
    return reserva


# ---------------------------------------------------------------------------
# Scope de oficina
# ---------------------------------------------------------------------------


def test_un_usuario_de_palma_no_ve_entregas_de_otra_oficina(
    economico, palma, aeropuerto, de_palma, coche
):
    """Test obligatorio: ni en las listas ni en los contadores."""
    mia = _reserva_de_hoy(economico, palma, vehiculo=coche)
    ajena = _reserva_de_hoy(economico, aeropuerto)

    panel = build_dashboard(user=de_palma)

    numeros = [reserva.number for reserva in panel.pickups]
    assert mia.number in numeros
    assert ajena.number not in numeros
    assert panel.stats.pickups_today == 1
    assert panel.stats.active_reservations == 1


def test_los_contadores_de_flota_tambien_respetan_el_scope(
    economico, palma, aeropuerto, de_palma, coche
):
    VehicleFactory(plate="9999AJE", category=economico, current_office=aeropuerto)

    panel = build_dashboard(user=de_palma)

    assert panel.stats.fleet_total == 1


def test_el_pendiente_total_solo_cuenta_lo_de_sus_oficinas(
    economico, palma, aeropuerto, de_palma, coche, tarifa
):
    _reserva_de_hoy(economico, palma, vehiculo=coche)
    _reserva_de_hoy(economico, aeropuerto)

    panel = build_dashboard(user=de_palma)
    del_ajeno = build_dashboard(user=de_palma, office=aeropuerto)

    assert panel.stats.pending_amount > 0
    # Pedir una oficina ajena no ensena nada: no esta en su alcance.
    assert del_ajeno.stats.pending_amount == Decimal("0.00")
    assert del_ajeno.pickups == []


def test_una_oficina_ajena_por_la_url_no_cuela(client, economico, aeropuerto, de_palma):
    _reserva_de_hoy(economico, aeropuerto)
    client.force_login(de_palma)

    respuesta = client.get(reverse("core:home"), {"office": aeropuerto.pk})

    assert respuesta.status_code == 200
    assert respuesta.context["panel"].pickups == []


# ---------------------------------------------------------------------------
# Selector de oficina
# ---------------------------------------------------------------------------


def test_con_una_sola_oficina_no_hay_selector(client, de_palma, palma):
    """Criterio de aceptacion: y los datos ya vienen filtrados."""
    client.force_login(de_palma)

    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert not respuesta.context["panel"].show_office_picker
    assert 'name="office"' not in contenido


def test_con_dos_oficinas_si_hay_selector(client, de_las_dos, palma, aeropuerto):
    client.force_login(de_las_dos)

    respuesta = client.get(reverse("core:home"))
    contenido = respuesta.content.decode()

    assert respuesta.context["panel"].show_office_picker
    assert 'name="office"' in contenido
    assert palma.name in contenido
    assert aeropuerto.name in contenido


def test_el_filtro_recorta_a_una_oficina(economico, palma, aeropuerto, de_las_dos, coche):
    _reserva_de_hoy(economico, palma, vehiculo=coche)
    _reserva_de_hoy(economico, aeropuerto)

    todas = build_dashboard(user=de_las_dos)
    solo_palma = build_dashboard(user=de_las_dos, office=palma)

    assert todas.stats.pickups_today == 2
    assert solo_palma.stats.pickups_today == 1


def test_un_usuario_sin_oficinas_ve_el_panel_vacio(db, rol_mostrador):
    huerfano = UserFactory(email="sinoficina@ejemplo.es", role=rol_mostrador)

    panel = build_dashboard(user=huerfano)

    assert panel.pickups == []
    assert panel.stats.fleet_total == 0
    assert panel.stats.pending_amount == Decimal("0.00")


# ---------------------------------------------------------------------------
# Contenido
# ---------------------------------------------------------------------------


def test_las_entregas_de_hoy_traen_lo_que_hace_falta(economico, palma, de_palma, coche, tarifa):
    reserva = create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=CustomerFactory(first_name="Ana", last_name="Garcia"),
        pickup_at=_hoy_a_las(9),
        return_at=_hoy_a_las(9) + timedelta(days=2),
        actor=de_palma,
    )
    assign_vehicle(reservation=reserva, vehicle=coche, actor=de_palma)

    panel = build_dashboard(user=de_palma)
    fila = panel.pickups[0]

    assert fila.number == reserva.number
    assert fila.customer.full_name.startswith("Ana")
    assert fila.vehicle == coche
    assert fila.pendiente == reserva.total, "sin cobros, se debe todo"


def test_el_pendiente_descuenta_lo_ya_cobrado(economico, palma, de_palma, coche, tarifa):
    reserva = create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=CustomerFactory(),
        pickup_at=_hoy_a_las(9),
        return_at=_hoy_a_las(9) + timedelta(days=2),
        actor=de_palma,
    )
    register_payment(
        reservation=reserva,
        amount=Decimal("50.00"),
        method=PaymentMethod.CARD,
        actor=de_palma,
    )

    panel = build_dashboard(user=de_palma)

    assert panel.pickups[0].pendiente == reserva.total - Decimal("50.00")


def test_la_fianza_no_baja_el_pendiente_del_panel(economico, palma, de_palma, coche, tarifa):
    reserva = create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=CustomerFactory(),
        pickup_at=_hoy_a_las(9),
        return_at=_hoy_a_las(9) + timedelta(days=2),
        actor=de_palma,
    )
    register_payment(
        reservation=reserva,
        amount=Decimal("150.00"),
        method=PaymentMethod.CASH,
        payment_type=PaymentType.DEPOSIT,
        actor=de_palma,
    )

    panel = build_dashboard(user=de_palma)

    assert panel.pickups[0].pendiente == reserva.total


def test_las_devoluciones_de_hoy_son_las_que_estan_fuera(economico, palma, de_palma, coche):
    en_curso = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        vehicle=coche,
        pickup_at=_hoy_a_las(9) - timedelta(days=2),
        return_at=_hoy_a_las(18),
        status=ReservationStatus.IN_PROGRESS,
        actual_pickup_at=_hoy_a_las(9) - timedelta(days=2),
    )
    _reserva_de_hoy(economico, palma)  # una confirmada no se devuelve hoy

    panel = build_dashboard(user=de_palma)

    assert [reserva.number for reserva in panel.returns] == [en_curso.number]


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------


def _alerta(panel, kind):
    return next((alerta for alerta in panel.alerts if alerta.kind == kind), None)


def test_avisa_de_las_entregas_de_hoy_sin_coche(economico, palma, de_palma, coche):
    sin_coche = _reserva_de_hoy(economico, palma)
    _reserva_de_hoy(economico, palma, hora=12, vehiculo=coche)

    panel = build_dashboard(user=de_palma)
    alerta = _alerta(panel, "sin_vehiculo")

    assert alerta.count == 1
    assert sin_coche.number in alerta.detail


def test_avisa_de_la_itv_y_el_seguro_a_punto_de_caducar(economico, palma, de_palma, coche):
    coche.itv_expiry = timezone.localdate() + timedelta(days=10)
    coche.save(update_fields=["itv_expiry"])
    VehicleFactory(
        plate="8888OKK",
        category=economico,
        current_office=palma,
        itv_expiry=timezone.localdate() + timedelta(days=200),
    )

    panel = build_dashboard(user=de_palma)
    alerta = _alerta(panel, "documentacion")

    assert alerta.count == 1
    assert coche.plate in alerta.detail


def test_avisa_de_las_devoluciones_con_retraso(economico, palma, de_palma, coche):
    tarde = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        vehicle=coche,
        pickup_at=timezone.now() - timedelta(days=5),
        return_at=timezone.now() - timedelta(hours=4),
        status=ReservationStatus.IN_PROGRESS,
    )

    panel = build_dashboard(user=de_palma)
    alerta = _alerta(panel, "retraso")

    assert alerta.count == 1
    assert tarde.number in alerta.detail


def test_avisa_de_finalizadas_sin_cobrar(economico, palma, de_palma, coche):
    finalizada = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        pickup_at=timezone.now() - timedelta(days=10),
        return_at=timezone.now() - timedelta(days=7),
        status=ReservationStatus.FINISHED,
        total=Decimal("120.00"),
    )

    panel = build_dashboard(user=de_palma)
    alerta = _alerta(panel, "sin_cobrar")

    assert alerta.count == 1
    assert finalizada.number in alerta.detail


def test_una_finalizada_ya_cobrada_no_alerta(economico, palma, de_palma, coche):
    finalizada = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        pickup_at=timezone.now() - timedelta(days=10),
        return_at=timezone.now() - timedelta(days=7),
        status=ReservationStatus.FINISHED,
        total=Decimal("120.00"),
    )
    register_payment(
        reservation=finalizada,
        amount=Decimal("120.00"),
        method=PaymentMethod.CARD,
        actor=de_palma,
    )

    panel = build_dashboard(user=de_palma)

    assert _alerta(panel, "sin_cobrar") is None


def test_sin_nada_que_avisar_no_hay_alertas(economico, palma, de_palma, coche):
    panel = build_dashboard(user=de_palma)

    assert panel.alerts == []


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------


def test_la_ocupacion_es_el_porcentaje_de_coches_fuera(economico, palma, de_palma, coche):
    for i in range(3):
        VehicleFactory(plate=f"70{i:02d}OCU", category=economico, current_office=palma)
    coche.status = VehicleStatus.RENTED
    coche.save(update_fields=["status"])

    panel = build_dashboard(user=de_palma)

    assert panel.stats.fleet_total == 4
    assert panel.stats.rented == 1
    assert panel.stats.occupancy == 25


def test_sin_flota_la_ocupacion_es_cero_y_no_revienta(palma, de_palma):
    panel = build_dashboard(user=de_palma)

    assert panel.stats.fleet_total == 0
    assert panel.stats.occupancy == 0


# ---------------------------------------------------------------------------
# Rendimiento
# ---------------------------------------------------------------------------


def _llenar(economico, palma, *, reservas=200, vehiculos=30):
    """Datos de sobra para que se note cualquier consulta por fila."""
    ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
    for i in range(vehiculos):
        VehicleFactory(plate=f"50{i:03d}VOL", category=economico, current_office=palma)

    clientes = [CustomerFactory() for _ in range(20)]
    reservas_creadas = []
    for i in range(reservas):
        # Un tercio hoy, el resto repartido por el ano.
        dias = 0 if i % 3 == 0 else -(i % 365) - 1
        inicio = ahora + timedelta(days=dias)
        reservas_creadas.append(
            ReservationFactory(
                category=economico,
                pickup_office=palma,
                return_office=palma,
                customer=clientes[i % len(clientes)],
                pickup_at=inicio,
                return_at=inicio + timedelta(days=3),
                status=(ReservationStatus.CONFIRMED if dias == 0 else ReservationStatus.FINISHED),
                total=Decimal("150.00"),
            )
        )
    return reservas_creadas


def test_el_panel_no_hace_una_consulta_por_fila(
    economico, palma, de_palma, coche, django_assert_max_num_queries
):
    """Test obligatorio: el numero de consultas no depende de los datos."""
    _llenar(economico, palma, reservas=120, vehiculos=15)

    # Once consultas: oficinas, entregas, devoluciones, ITV, retrasos, sin
    # cobrar, fianzas retenidas, activas, flota por estado, pendiente total y
    # prevision de ocupacion. Con la caja, una mas.
    with django_assert_max_num_queries(11):
        build_dashboard(user=de_palma)

    with django_assert_max_num_queries(12):
        build_dashboard(user=de_palma, include_cash=True)


def test_el_numero_de_consultas_no_crece_con_los_datos(economico, palma, de_palma, coche):
    """Con diez veces mas datos, exactamente las mismas consultas."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _llenar(economico, palma, reservas=20, vehiculos=5)
    with CaptureQueriesContext(connection) as pocas:
        build_dashboard(user=de_palma)

    _llenar(economico, palma, reservas=200, vehiculos=25)
    with CaptureQueriesContext(connection) as muchas:
        build_dashboard(user=de_palma)

    assert len(pocas) == len(muchas)


def test_la_pantalla_entera_tampoco_se_dispara(
    client, economico, palma, de_palma, coche, django_assert_max_num_queries
):
    """La vista suma sesion, usuario y permisos a las del panel."""
    _llenar(economico, palma, reservas=60, vehiculos=10)
    client.force_login(de_palma)

    with django_assert_max_num_queries(20):
        respuesta = client.get(reverse("core:home"))

    assert respuesta.status_code == 200


@pytest.mark.slow
def test_el_panel_carga_rapido_con_un_ano_de_datos(economico, palma, de_palma, coche):
    """Criterio de aceptacion: menos de 500 ms con datos de un ano.

    Se mide el armado del panel, que es lo que depende del volumen; el
    renderizado de la plantilla no crece con los datos historicos porque cada
    bloque esta acotado.
    """
    import time

    _llenar(economico, palma, reservas=3000, vehiculos=60)

    # Una pasada previa: interesa el coste en caliente, no el primer arranque.
    build_dashboard(user=de_palma)

    inicio = time.perf_counter()
    panel = build_dashboard(user=de_palma)
    tardanza = (time.perf_counter() - inicio) * 1000

    assert panel.stats.active_reservations > 0
    assert tardanza < 500, f"el panel tardo {tardanza:.0f} ms"


# ---------------------------------------------------------------------------
# Periodo
# ---------------------------------------------------------------------------


def test_manana_ensena_las_entregas_de_manana_y_no_las_de_hoy(economico, palma, de_palma, coche):
    hoy = _reserva_de_hoy(economico, palma, vehiculo=coche)
    manana = _reserva_de_hoy(economico, palma, hora=11)
    manana.pickup_at += timedelta(days=1)
    manana.return_at += timedelta(days=1)
    manana.save(update_fields=["pickup_at", "return_at"])

    panel = build_dashboard(user=de_palma, period=PERIODS["manana"])

    assert [reserva.number for reserva in panel.pickups] == [manana.number]
    assert hoy.number not in [reserva.number for reserva in panel.pickups]


def test_la_semana_cuenta_devoluciones_que_aun_no_han_salido(economico, palma, de_palma, coche):
    """Mirando hacia delante, una confirmada que vuelve esta semana tambien cuenta."""
    confirmada = _reserva_de_hoy(economico, palma, vehiculo=coche)

    hoy = build_dashboard(user=de_palma)
    semana = build_dashboard(user=de_palma, period=PERIODS["semana"])

    assert hoy.returns == []
    assert [reserva.number for reserva in semana.returns] == [confirmada.number]


def test_un_periodo_desconocido_cae_en_hoy():
    assert period_for("pasado") is PERIODS["hoy"]
    assert period_for(None) is PERIODS["hoy"]


def test_las_entregas_ya_hechas_salen_pero_no_cuentan_como_pendientes(
    economico, palma, de_palma, coche
):
    hecha = _reserva_de_hoy(economico, palma, vehiculo=coche, status=ReservationStatus.IN_PROGRESS)
    _reserva_de_hoy(economico, palma, hora=12)

    panel = build_dashboard(user=de_palma)

    assert hecha.number in [reserva.number for reserva in panel.pickups]
    assert panel.stats.pickups_today == 1
    assert panel.stats.pickups_done == 1
    assert panel.stats.pickups_total == 2


def test_cuenta_las_reservas_creadas_hoy(economico, palma, de_palma, coche):
    _reserva_de_hoy(economico, palma, vehiculo=coche)
    _reserva_de_hoy(economico, palma, hora=12, status=ReservationStatus.DRAFT)

    panel = build_dashboard(user=de_palma)

    # El borrador no es una reserva todavia.
    assert panel.stats.created_today == 1


def test_la_agenda_junta_entregas_y_devoluciones_por_hora(economico, palma, de_palma, coche):
    entrega = _reserva_de_hoy(economico, palma, hora=12)
    devolucion = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        vehicle=coche,
        pickup_at=_hoy_a_las(9) - timedelta(days=2),
        return_at=_hoy_a_las(9),
        status=ReservationStatus.IN_PROGRESS,
    )

    panel = build_dashboard(user=de_palma)

    assert [linea.url for linea in panel.agenda] == [
        reverse("reservations:detail", args=[devolucion.pk]),
        reverse("reservations:detail", args=[entrega.pk]),
    ]
    assert panel.agenda[1].state == "sin_coche"


# ---------------------------------------------------------------------------
# Prevision de ocupacion
# ---------------------------------------------------------------------------


def test_la_prevision_cuenta_las_reservas_que_pisan_cada_dia(economico, palma, de_palma, coche):
    VehicleFactory(plate="7001PRE", category=economico, current_office=palma)
    # Dos coches; una reserva de hoy a dentro de tres dias.
    _reserva_de_hoy(economico, palma, vehiculo=coche)

    panel = build_dashboard(user=de_palma)
    dias = panel.forecast.days

    assert len(dias) == 14
    assert [dia.percent for dia in dias[:4]] == [50, 50, 50, 50]
    assert dias[4].percent == 0
    assert panel.forecast.peak.percent == 50


def test_la_prevision_no_mira_otras_oficinas(economico, palma, aeropuerto, de_palma, coche):
    _reserva_de_hoy(economico, aeropuerto)

    panel = build_dashboard(user=de_palma)

    assert all(dia.reservations == 0 for dia in panel.forecast.days)


def test_la_prevision_sin_flota_no_revienta(palma, de_palma):
    panel = build_dashboard(user=de_palma)

    assert all(dia.percent == 0 for dia in panel.forecast.days)
    assert panel.forecast.line


# ---------------------------------------------------------------------------
# Caja del dia y fianzas
# ---------------------------------------------------------------------------


def _cobrar(reserva, importe, metodo, actor, tipo=PaymentType.PAYMENT):
    return register_payment(
        reservation=reserva,
        amount=Decimal(importe),
        method=metodo,
        payment_type=tipo,
        actor=actor,
        allow_overpayment=True,
    )


def test_la_caja_solo_se_calcula_si_se_pide(economico, palma, de_palma, coche):
    panel = build_dashboard(user=de_palma)

    assert panel.cash is None


def test_la_caja_suma_lo_cobrado_hoy_por_medio_de_pago(economico, palma, de_palma, coche):
    reserva = _reserva_de_hoy(economico, palma, vehiculo=coche)
    _cobrar(reserva, "60.00", PaymentMethod.CARD, de_palma)
    _cobrar(reserva, "40.00", PaymentMethod.CASH, de_palma)
    # La fianza no es un ingreso: no suma en la caja, va aparte.
    _cobrar(reserva, "300.00", PaymentMethod.CARD, de_palma, tipo=PaymentType.DEPOSIT)

    caja = build_dashboard(user=de_palma, include_cash=True).cash

    assert caja.collected == Decimal("100.00")
    assert caja.deposits_held == Decimal("300.00")
    assert [(medio.label, medio.amount, medio.percent) for medio in caja.by_method] == [
        ("Efectivo", Decimal("40.00"), 40),
        ("Tarjeta", Decimal("60.00"), 60),
    ]


def test_la_caja_no_suma_cobros_de_otra_oficina(economico, palma, aeropuerto, de_palma, coche):
    ajena = _reserva_de_hoy(economico, aeropuerto)
    _cobrar(ajena, "80.00", PaymentMethod.CARD, de_palma)

    caja = build_dashboard(user=de_palma, include_cash=True).cash

    assert caja.collected == Decimal("0.00")
    assert caja.by_method == []


def test_avisa_de_fianzas_retenidas_en_reservas_cerradas(economico, palma, de_palma, coche):
    cerrada = ReservationFactory(
        category=economico,
        pickup_office=palma,
        return_office=palma,
        customer=CustomerFactory(),
        pickup_at=timezone.now() - timedelta(days=10),
        return_at=timezone.now() - timedelta(days=7),
        status=ReservationStatus.FINISHED,
        total=Decimal("0.00"),
    )
    _cobrar(cerrada, "300.00", PaymentMethod.CARD, de_palma, tipo=PaymentType.DEPOSIT)
    # Una fianza de una reserva aun abierta es normal: no avisa.
    abierta = _reserva_de_hoy(economico, palma, vehiculo=coche)
    _cobrar(abierta, "200.00", PaymentMethod.CARD, de_palma, tipo=PaymentType.DEPOSIT)

    alerta = _alerta(build_dashboard(user=de_palma), "fianzas")

    assert alerta.count == 1
    assert cerrada.number in alerta.detail
    assert "300" in alerta.label


@pytest.fixture
def gestor_con_caja(db, palma):
    rol = RoleFactory(
        code="gestor-caja",
        name="Gestor",
        permissions=["reservations.view_reservation", "billing.view_billing"],
    )
    return UserFactory(email="caja@ejemplo.es", role=rol, offices=[palma])


def test_la_pantalla_solo_ensena_la_caja_a_quien_ve_cobros(client, de_palma, gestor_con_caja):
    client.force_login(de_palma)
    sin_permiso = client.get(reverse("core:home"))

    client.force_login(gestor_con_caja)
    con_permiso = client.get(reverse("core:home"))

    assert sin_permiso.context["panel"].cash is None
    assert "Caja de hoy" not in sin_permiso.content.decode()
    assert con_permiso.context["panel"].cash is not None
    assert "Caja de hoy" in con_permiso.content.decode()


# ---------------------------------------------------------------------------
# Campana de avisos
# ---------------------------------------------------------------------------


def test_la_campana_trae_los_avisos_del_usuario(client, economico, palma, de_palma, coche):
    _reserva_de_hoy(economico, palma)
    client.force_login(de_palma)

    respuesta = client.get(reverse("core:alerts_menu"))

    assert respuesta.status_code == 200
    assert "sin coche asignado" in respuesta.content.decode()


def test_la_campana_sin_avisos_lo_dice(client, palma, de_palma):
    client.force_login(de_palma)

    contenido = client.get(reverse("core:alerts_menu")).content.decode()

    assert "Nada que avisar" in contenido


def test_la_campana_pide_sesion(client):
    respuesta = client.get(reverse("core:alerts_menu"))

    assert respuesta.status_code == 302
