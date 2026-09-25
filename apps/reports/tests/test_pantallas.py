"""Los informes con datos reales: permisos, scope de oficina y exportacion."""

import csv
from datetime import timedelta
from io import StringIO

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.tests.test_shell import grids_sin_columnas_en_movil
from apps.reports import exports, services

pytestmark = pytest.mark.django_db


def _leer_csv(respuesta) -> list[list[str]]:
    texto = respuesta.content.decode("utf-8-sig")
    return list(csv.reader(StringIO(texto), delimiter=exports.SEPARADOR))


# ---------------------------------------------------------------------------
# Ingresos por coche
# ---------------------------------------------------------------------------


def test_los_ingresos_se_imputan_al_coche_que_salio(analista, centro, ibiza, corsa, alquilar):
    alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=4)
    alquilar(vehiculo=corsa, oficina=centro, desde_dias=2, hasta_dias=3)

    hoy = timezone.localdate()
    filas = services.revenue_by_vehicle(user=analista, desde=hoy, hasta=hoy + timedelta(days=7))

    matriculas = {fila.plate for fila in filas}
    assert matriculas == {"1111AAA", "2222BBB"}
    assert all(fila.revenue > 0 for fila in filas)


def test_una_reserva_cancelada_no_cuenta_como_ingreso(analista, centro, ibiza, alquilar):
    from apps.reservations.models import ReservationStatus
    from apps.reservations.state_machine import transition

    reserva = alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=3)
    transition(reserva, ReservationStatus.CANCELLED, analista)

    hoy = timezone.localdate()
    periodo = {"desde": hoy, "hasta": hoy + timedelta(days=7)}

    assert services.revenue_by_vehicle(user=analista, **periodo) == []


def test_los_informes_respetan_el_scope_de_oficina(analista, norte, corsa, alquilar, db):
    """Quien solo trabaja en Centro no ve lo que factura Norte."""
    from apps.fleet.tests.factories import VehicleFactory

    ajeno = VehicleFactory(plate="9999ZZZ", category=corsa.category, current_office=norte)
    alquilar(vehiculo=ajeno, oficina=norte, desde_dias=1, hasta_dias=3)

    hoy = timezone.localdate()
    periodo = {"desde": hoy, "hasta": hoy + timedelta(days=7)}

    assert services.revenue_by_vehicle(user=analista, **periodo) == []


# ---------------------------------------------------------------------------
# Ocupacion
# ---------------------------------------------------------------------------


def test_la_ocupacion_cuenta_la_flota_activa(analista, centro, ibiza, corsa, alquilar):
    alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=3)

    hoy = timezone.localdate()
    filas = services.occupancy_by_month(user=analista, desde=hoy, hasta=hoy)

    assert len(filas) == 1
    assert filas[0].vehicles == 2
    assert filas[0].rented_days > 0


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------


def test_el_mostrador_no_entra_a_los_informes(client, mostrador):
    client.force_login(mostrador)

    assert client.get(reverse("reports:reports")).status_code == 403


def test_la_pantalla_ensena_los_dos_informes(client, analista, centro, ibiza, alquilar):
    alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=3)
    client.force_login(analista)

    hoy = timezone.localdate()
    contenido = client.get(
        reverse("reports:reports"),
        {"desde": hoy.isoformat(), "hasta": (hoy + timedelta(days=7)).isoformat()},
    ).content.decode()

    assert "Ingresos por coche" in contenido
    assert "Ocupación por mes" in contenido
    assert "1111AAA" in contenido
    # Regresion: la tabla de ingresos ensanchaba la pagina en movil.
    assert grids_sin_columnas_en_movil(contenido) == []


def test_un_rango_al_reves_se_endereza(client, analista):
    client.force_login(analista)

    respuesta = client.get(
        reverse("reports:reports"), {"desde": "2026-09-30", "hasta": "2026-09-01"}
    )

    assert respuesta.status_code == 200
    assert respuesta.context["desde"].isoformat() == "2026-09-01"
    assert respuesta.context["hasta"].isoformat() == "2026-09-30"


def test_una_fecha_ilegible_no_rompe_la_pantalla(client, analista):
    client.force_login(analista)

    respuesta = client.get(reverse("reports:reports"), {"desde": "ayer por la tarde"})

    assert respuesta.status_code == 200


# ---------------------------------------------------------------------------
# Exportacion para la gestoria
# ---------------------------------------------------------------------------


def test_el_csv_de_cobros_trae_lo_cobrado(client, analista, centro, ibiza, alquilar):
    from decimal import Decimal

    from apps.billing.models import PaymentMethod
    from apps.billing.services import register_payment

    reserva = alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=3)
    register_payment(
        reservation=reserva,
        amount=Decimal("50.00"),
        method=PaymentMethod.CASH,
        office=centro,
    )
    client.force_login(analista)
    hoy = timezone.localdate().isoformat()

    respuesta = client.get(reverse("reports:export_payments"), {"desde": hoy, "hasta": hoy})

    assert respuesta.status_code == 200
    assert respuesta["Content-Type"].startswith("text/csv")
    assert "attachment" in respuesta["Content-Disposition"]

    filas = _leer_csv(respuesta)
    assert filas[0][0] == "Fecha"
    assert len(filas) == 2
    # Importe con coma decimal: es lo que entiende Excel en espanol.
    assert "50,00" in filas[1]
    assert reserva.number in filas[1]


def test_el_csv_lleva_bom_para_que_excel_respete_los_acentos(client, analista):
    client.force_login(analista)

    respuesta = client.get(reverse("reports:export_invoices"))

    assert respuesta.content.startswith(b"\xef\xbb\xbf")


def test_el_csv_de_facturas_trae_base_iva_y_total(client, analista, centro, ibiza, alquilar, db):
    from decimal import Decimal

    from apps.accounts.tests.factories import RoleFactory, UserFactory
    from apps.billing.models import PaymentMethod
    from apps.billing.services import issue_invoice, register_payment
    from apps.reservations.models import Reservation, ReservationStatus
    from apps.settings_app.models import CompanySettings

    empresa = CompanySettings.load()
    empresa.legal_name = "Alquileres Centro, S.L."
    empresa.tax_id = "B07456123"
    empresa.address = "Calle Mayor, 1"
    empresa.city = "Palma"
    empresa.postal_code = "07001"
    empresa.save()

    reserva = alquilar(vehiculo=ibiza, oficina=centro, desde_dias=1, hasta_dias=3)
    register_payment(
        reservation=reserva,
        amount=reserva.grand_total,
        method=PaymentMethod.CASH,
        office=centro,
    )
    Reservation.objects.filter(pk=reserva.pk).update(status=ReservationStatus.FINISHED)
    reserva.refresh_from_db()

    facturador = UserFactory(
        email="facturador-informes@ejemplo.es",
        role=RoleFactory(
            code="facturador-informes",
            name="Facturacion",
            permissions=["billing.add_invoice", "billing.view_billing"],
        ),
        offices=[centro],
    )
    factura = issue_invoice(reservation=reserva, actor=facturador)

    client.force_login(analista)
    hoy = timezone.localdate().isoformat()
    filas = _leer_csv(client.get(reverse("reports:export_invoices"), {"desde": hoy, "hasta": hoy}))

    assert filas[0][:3] == ["Numero", "Fecha", "Serie"]
    assert len(filas) == 2
    assert filas[1][0] == factura.number
    assert Decimal(filas[1][7].replace(",", ".")) == factura.base_amount
    assert Decimal(filas[1][9].replace(",", ".")) == factura.total


def test_sin_permiso_no_hay_exportacion(client, mostrador):
    client.force_login(mostrador)

    assert client.get(reverse("reports:export_invoices")).status_code == 403
    assert client.get(reverse("reports:export_payments")).status_code == 403
