"""Extras, precio manual y recalculo."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.pricing.models import CalculationType
from apps.reservations.models import PriceChangeKind, ReservationExtra, ReservationPriceChange
from apps.reservations.services import (
    ManualPriceWouldBeLost,
    ReservationServiceError,
    add_extra,
    apply_change,
    create_quick_reservation,
    recalculate_price,
    remove_extra,
    set_extra_quantity,
    set_manual_price,
)

from .factories import ExtraFactory, en

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserva(economico, palma, cliente, coche, tarifa, agente):
    return create_quick_reservation(
        category=economico,
        pickup_office=palma,
        customer=cliente,
        pickup_at=en(1),
        return_at=en(4),
        actor=agente,
    )


@pytest.fixture
def silla(db):
    return ExtraFactory(
        code="silla",
        name="Silla infantil",
        price=Decimal("5.00"),
        tax_rate=Decimal("21.00"),
        calculation_type=CalculationType.PER_DAY,
        max_quantity=3,
    )


@pytest.fixture
def con_precio_manual(reserva, responsable_precio):
    return set_manual_price(
        reservation=reserva,
        daily_price=Decimal("30.00"),
        reason="Cliente de empresa, precio pactado.",
        actor=responsable_precio,
    )


@pytest.fixture
def responsable_precio(db, palma):
    """Usuario con permiso para tocar el precio a mano."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    return UserFactory(
        email="precios@ejemplo.es",
        role=RoleFactory(
            code="precios",
            name="Precios",
            permissions=[
                "reservations.view_reservation",
                "reservations.change_reservation",
                "reservations.change_reservation_price",
            ],
        ),
        offices=[palma],
    )


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------


def test_anadir_un_extra_recalcula_el_total(reserva, silla, agente):
    """3 dias a 45 EUR (135) + silla 5 EUR/dia (15) + 21% = 181,50."""
    antes = reserva.total
    assert antes == Decimal("163.35")

    con_silla = add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)

    assert con_silla.extras_total == Decimal("15.00")
    assert con_silla.total == Decimal("181.50")
    assert con_silla.total > antes
    linea = ReservationExtra.objects.get(reservation=con_silla)
    assert linea.concept == "Silla infantil (3 dias)"


def test_quitar_el_extra_devuelve_el_total(reserva, silla, agente):
    antes = reserva.total
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)
    linea = ReservationExtra.objects.get(reservation=reserva)

    sin_silla = remove_extra(reservation=reserva, line=linea, actor=agente)

    assert sin_silla.total == antes
    assert not ReservationExtra.objects.exists()


def test_cambiar_la_cantidad_recalcula(reserva, silla, agente):
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)
    linea = ReservationExtra.objects.get(reservation=reserva)

    con_dos = set_extra_quantity(reservation=reserva, line=linea, quantity=2, actor=agente)

    assert con_dos.extras_total == Decimal("30.00")
    assert con_dos.total == Decimal("199.65")


def test_el_tope_de_cantidad_se_respeta(reserva, silla, agente):
    with pytest.raises(ReservationServiceError) as fallo:
        add_extra(reservation=reserva, extra=silla, quantity=4, actor=agente)

    assert "no se pueden poner mas de 3" in str(fallo.value)
    assert not ReservationExtra.objects.exists()


def test_el_mismo_extra_no_se_pone_dos_veces(reserva, silla, agente):
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)

    with pytest.raises(ReservationServiceError):
        add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)


def test_el_tope_de_importe_del_extra_se_aplica(reserva, agente, db):
    """5 EUR/dia con tope de 10 EUR: tres dias no cobran 15."""
    seguro = ExtraFactory(
        code="seguro",
        name="Seguro",
        price=Decimal("5.00"),
        calculation_type=CalculationType.PER_DAY,
        max_amount=Decimal("10.00"),
    )

    con_seguro = add_extra(reservation=reserva, extra=seguro, quantity=1, actor=agente)

    assert con_seguro.extras_total == Decimal("10.00")


def test_anadir_un_extra_deja_rastro(reserva, silla, agente):
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)

    cambio = ReservationPriceChange.objects.get(reservation=reserva)
    assert cambio.kind == PriceChangeKind.EXTRAS
    assert cambio.previous_total == Decimal("163.35")
    assert cambio.new_total == Decimal("181.50")
    assert cambio.changed_by == agente


# ---------------------------------------------------------------------------
# Precio manual
# ---------------------------------------------------------------------------


def test_el_precio_manual_exige_permiso(reserva, agente):
    """El agente puede editar la reserva, pero no tocar el precio."""
    assert not agente.has_perm("reservations.change_reservation_price")

    with pytest.raises(PermissionDenied):
        set_manual_price(
            reservation=reserva, daily_price=Decimal("10.00"), reason="porque si", actor=agente
        )

    reserva.refresh_from_db()
    assert not reserva.is_price_manual
    assert not ReservationPriceChange.objects.exists()


def test_el_precio_manual_exige_motivo(reserva, responsable_precio):
    for motivo in ("", "   "):
        with pytest.raises(ReservationServiceError) as fallo:
            set_manual_price(
                reservation=reserva,
                daily_price=Decimal("30.00"),
                reason=motivo,
                actor=responsable_precio,
            )
        assert "por que" in str(fallo.value)

    reserva.refresh_from_db()
    assert not reserva.is_price_manual


def test_el_precio_manual_se_aplica_y_queda_marcado(reserva, responsable_precio):
    """30 EUR/dia x 3 dias = 90 + 21% = 108,90."""
    manual = set_manual_price(
        reservation=reserva,
        daily_price=Decimal("30.00"),
        reason="Cliente de empresa.",
        actor=responsable_precio,
    )

    assert manual.is_price_manual
    assert manual.manual_price_reason == "Cliente de empresa."
    assert manual.base_amount == Decimal("90.00")
    assert manual.total == Decimal("108.90")


def test_el_precio_manual_queda_en_auditoria(reserva, responsable_precio):
    set_manual_price(
        reservation=reserva,
        daily_price=Decimal("30.00"),
        reason="Cliente de empresa.",
        actor=responsable_precio,
    )

    cambio = ReservationPriceChange.objects.get(reservation=reserva)
    assert cambio.kind == PriceChangeKind.MANUAL
    assert cambio.reason == "Cliente de empresa."
    assert cambio.changed_by == responsable_precio
    assert cambio.difference < 0


def test_el_desglose_dice_que_el_precio_es_manual(con_precio_manual):
    avisos = con_precio_manual.price_breakdown["warnings"]

    assert any("forzado a mano" in aviso for aviso in avisos)


# ---------------------------------------------------------------------------
# El precio manual no se pisa en silencio
# ---------------------------------------------------------------------------


def test_cambiar_fechas_con_precio_manual_pide_confirmacion(con_precio_manual, agente):
    """El error clasico: tocar fechas y perder el precio acordado sin enterarse."""
    total_pactado = con_precio_manual.total

    with pytest.raises(ManualPriceWouldBeLost) as fallo:
        apply_change(
            reservation=con_precio_manual,
            return_at=con_precio_manual.return_at + timedelta(days=2),
            actor=agente,
        )

    assert "puesto a mano" in str(fallo.value)
    con_precio_manual.refresh_from_db()
    assert con_precio_manual.total == total_pactado, "no se ha tocado nada"
    assert con_precio_manual.is_price_manual


def test_confirmando_si_se_recalcula(con_precio_manual, agente):
    cambiada = apply_change(
        reservation=con_precio_manual,
        return_at=con_precio_manual.return_at + timedelta(days=2),
        confirm_manual_override=True,
        actor=agente,
    )

    assert not cambiada.is_price_manual
    assert cambiada.manual_price_reason == ""
    assert cambiada.base_amount == Decimal("200.00")  # 5 dias a tarifa


def test_anadir_un_extra_con_precio_manual_pide_confirmacion(con_precio_manual, silla, agente):
    with pytest.raises(ManualPriceWouldBeLost):
        add_extra(reservation=con_precio_manual, extra=silla, quantity=1, actor=agente)

    assert not ReservationExtra.objects.exists()


def test_quitar_un_extra_con_precio_manual_pide_confirmacion(
    reserva, silla, agente, responsable_precio
):
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)
    linea = ReservationExtra.objects.get(reservation=reserva)
    set_manual_price(
        reservation=reserva,
        daily_price=Decimal("30.00"),
        reason="Pactado.",
        actor=responsable_precio,
    )

    with pytest.raises(ManualPriceWouldBeLost):
        remove_extra(reservation=reserva, line=linea, actor=agente)

    assert ReservationExtra.objects.count() == 1


def test_el_preview_de_cambio_avisa_del_precio_manual(con_precio_manual, agente):
    from apps.reservations.services import preview_change

    preview = preview_change(reservation=con_precio_manual, return_at=en(6), actor=agente)

    assert "puesto a mano" in preview.manual_price_warning


# ---------------------------------------------------------------------------
# Recalcular
# ---------------------------------------------------------------------------


def test_recalcular_devuelve_el_precio_de_tarifa(con_precio_manual, agente):
    recalculada = recalculate_price(
        reservation=con_precio_manual, confirm_manual_override=True, actor=agente
    )

    assert not recalculada.is_price_manual
    assert recalculada.total == Decimal("163.35")
    cambio = ReservationPriceChange.objects.filter(kind=PriceChangeKind.RECALCULATED).get()
    assert cambio.previous_total == Decimal("108.90")


def test_recalcular_sin_confirmar_no_pisa_el_precio_manual(con_precio_manual, agente):
    with pytest.raises(ManualPriceWouldBeLost):
        recalculate_price(reservation=con_precio_manual, actor=agente)

    con_precio_manual.refresh_from_db()
    assert con_precio_manual.is_price_manual


# ---------------------------------------------------------------------------
# Desde la pantalla
# ---------------------------------------------------------------------------


def test_sin_permiso_no_se_toca_el_precio_ni_por_post(client, reserva, agente):
    """Criterio: ni el boton ni el endpoint."""
    client.force_login(agente)
    url = reverse("reservations:manual_price", args=[reserva.pk])

    assert client.get(url).status_code == 403
    assert client.post(url, {"daily_price": "1.00", "reason": "colado"}).status_code == 403

    reserva.refresh_from_db()
    assert not reserva.is_price_manual
    assert reserva.total == Decimal("163.35")


def test_la_pestana_de_precio_ensena_tarifa_y_tramo(client, reserva, agente, tarifa):
    client.force_login(agente)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "precio"])
    ).content.decode()

    assert "Tarifa aplicada" in contenido
    assert tarifa.name in contenido
    assert "Tramo" in contenido
    assert "Dias facturados" in contenido


def test_el_modal_de_extra_avisa_del_precio_manual(client, con_precio_manual, silla, agente):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:extra_add", args=[con_precio_manual.pk]),
        {"extra": silla.pk, "quantity": 1},
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 422
    assert "puesto a mano" in contenido
    assert 'name="confirm_manual"' in contenido
    assert not ReservationExtra.objects.exists()


def test_confirmando_desde_el_modal_el_extra_entra(client, con_precio_manual, silla, agente):
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:extra_add", args=[con_precio_manual.pk]),
        {"extra": silla.pk, "quantity": 1, "confirm_manual": "1"},
    )

    assert respuesta.status_code == 200
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    assert ReservationExtra.objects.count() == 1
    con_precio_manual.refresh_from_db()
    assert not con_precio_manual.is_price_manual


def test_quitar_extra_con_precio_manual_devuelve_el_dialogo(
    client, reserva, silla, agente, responsable_precio
):
    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)
    linea = ReservationExtra.objects.get()
    set_manual_price(
        reservation=reserva,
        daily_price=Decimal("30.00"),
        reason="Pactado.",
        actor=responsable_precio,
    )
    client.force_login(agente)

    respuesta = client.post(
        reverse("reservations:extra_line", args=[reserva.pk, linea.pk]), {"accion": "quitar"}
    )

    assert respuesta.status_code == 409
    assert "Recalcular de todos modos" in respuesta.content.decode()
    assert ReservationExtra.objects.count() == 1


def test_el_precio_manual_desde_la_pantalla(client, reserva, responsable_precio):
    client.force_login(responsable_precio)

    respuesta = client.post(
        reverse("reservations:manual_price", args=[reserva.pk]),
        {"daily_price": "30.00", "reason": "Cliente de empresa."},
    )

    reserva.refresh_from_db()
    assert respuesta.status_code == 200
    assert reserva.is_price_manual
    assert reserva.total == Decimal("108.90")


def test_el_historial_incluye_los_cambios_de_precio(reserva, silla, agente):
    """El precio es parte de la historia de la reserva, no un dato suelto."""
    from apps.reservations.selectors import timeline

    add_extra(reservation=reserva, extra=silla, quantity=1, actor=agente)

    lineas = timeline(reserva)
    de_precio = [linea for linea in lineas if linea.source == "price"]

    assert len(de_precio) == 1
    assert "163,35" in de_precio[0].title or "163.35" in de_precio[0].title
    assert "Silla infantil" in de_precio[0].detail
