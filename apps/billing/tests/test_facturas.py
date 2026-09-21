"""Facturas: emision, numeracion sin huecos, cadena de huellas y rectificativas."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.billing.models import (
    Invoice,
    InvoiceImmutable,
    InvoiceKind,
    InvoiceSeries,
)
from apps.billing.selectors import has_issued_invoice, issued_invoice_for, reservations_to_invoice
from apps.billing.services import (
    InvoiceServiceError,
    issue_invoice,
    rectify_invoice,
    save_series,
    set_series_active,
)
from apps.reservations.models import Reservation

pytestmark = pytest.mark.django_db

TOTAL = Decimal("163.35")


def _anio() -> str:
    return str(timezone.localdate().year)


# ---------------------------------------------------------------------------
# Emision
# ---------------------------------------------------------------------------


def test_la_factura_suma_sus_lineas_y_cuadra_con_la_reserva(finalizada, facturador):
    factura = issue_invoice(reservation=finalizada, actor=facturador)

    lineas = list(factura.lines.all())
    assert factura.number == f"F{_anio()}-00001"
    assert factura.kind == InvoiceKind.ORDINARY
    assert factura.total == TOTAL == finalizada.grand_total
    assert factura.total == sum(linea.total for linea in lineas)
    assert factura.base_amount == sum(linea.base_amount for linea in lineas)
    assert factura.tax_amount == sum(linea.tax_amount for linea in lineas)
    assert [linea.position for linea in lineas] == list(range(1, len(lineas) + 1))


def test_la_factura_lleva_tambien_los_cargos_de_la_devolucion(finalizada, facturador):
    from apps.reservations.models import ChargeKind, ReservationCharge

    ReservationCharge.objects.create(
        reservation=finalizada,
        kind=ChargeKind.FUEL,
        concept="Combustible",
        quantity=Decimal("10"),
        unit_price=Decimal("1.60"),
        tax_rate=Decimal("21.00"),
        base_amount=Decimal("16.00"),
        tax_amount=Decimal("3.36"),
        total=Decimal("19.36"),
    )
    Reservation.objects.filter(pk=finalizada.pk).update(charges_total=Decimal("19.36"))
    finalizada.refresh_from_db()

    factura = issue_invoice(reservation=finalizada, actor=facturador)

    assert factura.total == TOTAL + Decimal("19.36")
    assert factura.lines.filter(concept="Combustible").exists()


def test_la_factura_copia_los_datos_fiscales_al_emitir(finalizada, facturador, empresa):
    factura = issue_invoice(reservation=finalizada, actor=facturador)

    cliente = finalizada.customer
    cliente.address = "Otra calle, 99"
    cliente.save()
    empresa.legal_name = "Nombre nuevo, S.L."
    empresa.save()
    factura.refresh_from_db()

    assert factura.issuer_name == "Alquileres Centro, S.L."
    assert factura.issuer_tax_id == "B07456123"
    assert factura.customer_name == cliente.full_name
    assert "Otra calle" not in factura.customer_address


def test_la_numeracion_es_correlativa_y_sin_huecos(finalizada, otra_finalizada, facturador):
    primera = issue_invoice(reservation=finalizada, actor=facturador)
    segunda = issue_invoice(reservation=otra_finalizada, actor=facturador)

    assert (primera.sequence, segunda.sequence) == (1, 2)
    assert segunda.number == f"F{_anio()}-00002"


def test_un_error_despues_de_numerar_no_deja_hueco(
    finalizada, otra_finalizada, facturador, monkeypatch
):
    """El numero ya estaba tomado cuando falla: la transaccion lo devuelve."""
    from apps.billing import services

    def revienta():
        raise RuntimeError("fallo a mitad de emision")

    with monkeypatch.context() as parche:
        parche.setattr(services, "_huella_anterior", revienta)
        with pytest.raises(RuntimeError):
            issue_invoice(reservation=finalizada, actor=facturador)

    factura = issue_invoice(reservation=otra_finalizada, actor=facturador)

    assert factura.sequence == 1
    assert not Invoice.objects.filter(reservation=finalizada).exists()


def test_cada_factura_encadena_la_huella_de_la_anterior(finalizada, otra_finalizada, facturador):
    primera = issue_invoice(reservation=finalizada, actor=facturador)
    segunda = issue_invoice(reservation=otra_finalizada, actor=facturador)

    assert primera.hash_anterior == ""
    assert segunda.hash_anterior == primera.hash_actual
    assert len(segunda.hash_actual) == 64
    assert f"numserie={segunda.number}" in segunda.qr_data


def test_solo_se_factura_lo_finalizado(reserva, facturador):
    with pytest.raises(InvoiceServiceError, match="finalizadas"):
        issue_invoice(reservation=reserva, actor=facturador)
    assert not Invoice.objects.exists()


def test_una_reserva_no_se_factura_dos_veces(finalizada, facturador):
    issue_invoice(reservation=finalizada, actor=facturador)

    with pytest.raises(InvoiceServiceError, match="en vigor"):
        issue_invoice(reservation=finalizada, actor=facturador)


def test_sin_permiso_no_se_emite(finalizada, cajero):
    with pytest.raises(PermissionDenied):
        issue_invoice(reservation=finalizada, actor=cajero)


def test_con_la_empresa_sin_configurar_no_se_emite(finalizada, facturador, empresa):
    empresa.tax_id = "00000000"
    empresa.save()

    with pytest.raises(InvoiceServiceError, match="datos fiscales"):
        issue_invoice(reservation=finalizada, actor=facturador)


def test_sin_serie_activa_no_se_emite(finalizada, facturador):
    InvoiceSeries.objects.filter(kind=InvoiceKind.ORDINARY).update(is_active=False)

    with pytest.raises(InvoiceServiceError, match="serie"):
        issue_invoice(reservation=finalizada, actor=facturador)


def test_una_factura_emitida_no_se_edita_ni_se_borra(finalizada, facturador):
    factura = issue_invoice(reservation=finalizada, actor=facturador)
    linea = factura.lines.first()

    factura.total = Decimal("1.00")
    with pytest.raises(InvoiceImmutable):
        factura.save()
    with pytest.raises(InvoiceImmutable):
        factura.delete()
    with pytest.raises(InvoiceImmutable):
        linea.save()


def test_la_reserva_facturada_no_cambia_de_precio(finalizada, facturador):
    """La guarda que ya existia en reservas ahora ve la factura de verdad."""
    from apps.reservations.services import InvoicedReservationError, set_manual_price

    issue_invoice(reservation=finalizada, actor=facturador)
    facturador.role.permissions.add(Permission.objects.get(codename="change_reservation_price"))
    facturador = type(facturador).objects.get(pk=facturador.pk)

    assert has_issued_invoice(finalizada)
    with pytest.raises(InvoicedReservationError):
        set_manual_price(
            reservation=finalizada, daily_price=Decimal("10"), reason="rebaja", actor=facturador
        )


# ---------------------------------------------------------------------------
# Rectificativas
# ---------------------------------------------------------------------------


def test_la_rectificativa_anula_la_original_en_negativo(finalizada, administrador):
    original = issue_invoice(reservation=finalizada, actor=administrador)

    rectificativa = rectify_invoice(invoice=original, reason="NIF erroneo", actor=administrador)

    assert rectificativa.kind == InvoiceKind.RECTIFYING
    assert rectificativa.number == f"FR{_anio()}-00001"
    assert rectificativa.rectifies == original
    assert rectificativa.total == -original.total
    assert rectificativa.verifactu_type == "R4"
    assert rectificativa.hash_anterior == original.hash_actual
    assert issued_invoice_for(finalizada) is None


def test_tras_rectificar_la_reserva_se_vuelve_a_facturar(finalizada, administrador):
    original = issue_invoice(reservation=finalizada, actor=administrador)
    rectify_invoice(invoice=original, reason="Cliente equivocado", actor=administrador)

    assert finalizada in reservations_to_invoice(administrador)
    nueva = issue_invoice(reservation=finalizada, actor=administrador)

    assert nueva.sequence == 2
    assert issued_invoice_for(finalizada) == nueva


def test_no_se_rectifica_dos_veces_ni_una_rectificativa(finalizada, administrador):
    original = issue_invoice(reservation=finalizada, actor=administrador)
    rectificativa = rectify_invoice(invoice=original, reason="Error", actor=administrador)

    with pytest.raises(InvoiceServiceError, match="ya esta rectificada"):
        rectify_invoice(invoice=original, reason="Otra vez", actor=administrador)
    with pytest.raises(InvoiceServiceError, match="no se rectifica"):
        rectify_invoice(invoice=rectificativa, reason="Error", actor=administrador)


def test_la_rectificativa_exige_motivo_y_permiso(finalizada, facturador, administrador):
    original = issue_invoice(reservation=finalizada, actor=facturador)

    with pytest.raises(PermissionDenied):
        rectify_invoice(invoice=original, reason="Error", actor=facturador)
    with pytest.raises(InvoiceServiceError, match="motivo"):
        rectify_invoice(invoice=original, reason="   ", actor=administrador)


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


def test_la_migracion_deja_una_serie_por_defecto_de_cada_tipo():
    """Se llama a la funcion de la migracion sobre una tabla vacia."""
    import importlib

    from django.apps import apps as registro

    migracion = importlib.import_module("apps.billing.migrations.0004_facturas_series_y_verifactu")
    # Una serie no se borra ni en bloque (es un maestro); para simular una
    # instalacion nueva se vacia la tabla por debajo del ORM.
    InvoiceSeries.objects.all()._raw_delete(InvoiceSeries.objects.db)

    migracion.crear_series_por_defecto(registro, None)

    por_defecto = InvoiceSeries.objects.filter(is_default=True, is_active=True)
    assert set(por_defecto.values_list("kind", "number_format")) == {
        (InvoiceKind.ORDINARY, "F{year}-{sequence:05d}"),
        (InvoiceKind.RECTIFYING, "FR{year}-{sequence:05d}"),
    }


def test_marcar_una_serie_por_defecto_desmarca_la_anterior(administrador):
    nueva = save_series(
        series=InvoiceSeries(
            code="WEB", name="Web", number_format="W{year}/{sequence:04d}", is_default=True
        ),
        actor=administrador,
    )

    assert nueva.code == "web"
    assert list(InvoiceSeries.objects.filter(kind=InvoiceKind.ORDINARY, is_default=True)) == [nueva]


@pytest.mark.parametrize("formato", ["F{year}", "F{year}-{otro}", "F{sequence:zz}", "SIN"])
def test_un_formato_que_no_numera_se_rechaza(administrador, formato):
    with pytest.raises(InvoiceServiceError):
        save_series(
            series=InvoiceSeries(code="mala", name="Mala", number_format=formato),
            actor=administrador,
        )


def test_una_serie_con_facturas_no_cambia_de_formato(finalizada, administrador):
    issue_invoice(reservation=finalizada, actor=administrador)
    serie = InvoiceSeries.objects.get(code="f")

    serie.number_format = "X{sequence}"
    with pytest.raises(InvoiceServiceError, match="ya tiene facturas"):
        save_series(series=serie, actor=administrador)


def test_la_serie_por_defecto_no_se_desactiva(administrador):
    serie = InvoiceSeries.objects.get(code="f")

    with pytest.raises(InvoiceServiceError, match="por defecto"):
        set_series_active(series=serie, active=False, actor=administrador)


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------


def test_la_factura_copia_las_politicas_que_salen_en_factura(finalizada, facturador):
    from apps.settings_app.models import Policy

    Policy.objects.create(title="Combustible", body="Lleno a lleno.", sort_order=2)
    Policy.objects.create(title="Cancelacion", body="48 horas.", sort_order=1)
    Policy.objects.create(title="Interna", body="No sale.", show_on_invoice=False)
    Policy.objects.create(title="Retirada", body="Tampoco.", is_active=False)

    factura = issue_invoice(reservation=finalizada, actor=facturador)
    Policy.objects.filter(title="Combustible").update(body="Texto nuevo.")
    factura.refresh_from_db()

    assert factura.policies == [
        {"title": "Cancelacion", "body": "48 horas."},
        {"title": "Combustible", "body": "Lleno a lleno."},
    ]
