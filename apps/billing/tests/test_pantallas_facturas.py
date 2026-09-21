"""Pantallas de facturacion: permisos, scope de oficina y flujo por HTMX."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.billing.models import Invoice, InvoiceSeries
from apps.billing.services import issue_invoice

pytestmark = pytest.mark.django_db


@pytest.fixture
def de_norte(db, norte):
    """Ve facturacion, pero solo de su oficina."""
    return UserFactory(
        email="norte@ejemplo.es",
        role=RoleFactory(
            code="facturacion-norte",
            name="Facturacion norte",
            permissions=["billing.view_billing", "reservations.view_reservation"],
        ),
        offices=[norte],
    )


@pytest.fixture
def factura(finalizada, facturador):
    return issue_invoice(reservation=finalizada, actor=facturador)


def test_el_listado_ensena_las_facturas_de_su_oficina(client, facturador, factura):
    client.force_login(facturador)

    contenido = client.get(reverse("billing:invoice_list")).content.decode()

    assert factura.number in contenido


def test_otra_oficina_no_ve_la_factura_ni_por_url(client, de_norte, factura):
    client.force_login(de_norte)

    listado = client.get(reverse("billing:invoice_list")).content.decode()
    ficha = client.get(reverse("billing:invoice_detail", args=[factura.pk]))
    pdf = client.get(reverse("billing:invoice_pdf", args=[factura.pk]))

    assert factura.number not in listado
    assert ficha.status_code == 404
    assert pdf.status_code == 404


def test_la_ficha_ensena_la_huella_y_el_qr(client, facturador, factura):
    client.force_login(facturador)

    contenido = client.get(reverse("billing:invoice_detail", args=[factura.pk])).content.decode()

    assert factura.hash_actual in contenido
    assert "<svg" in contenido


def test_el_pdf_sale_de_la_factura(client, facturador, factura):
    import io

    from pypdf import PdfReader

    client.force_login(facturador)

    respuesta = client.get(reverse("billing:invoice_pdf", args=[factura.pk]))
    texto = PdfReader(io.BytesIO(respuesta.content)).pages[0].extract_text()

    assert respuesta["Content-Type"] == "application/pdf"
    assert factura.number in texto
    assert "VERI*FACTU" not in texto  # todavia no se remite a la AEAT


def test_el_mostrador_puede_sacar_el_pdf_de_su_reserva(client, cajero, factura):
    """Sin permiso de facturacion, pero ve la reserva: tiene que poder imprimirla."""
    client.force_login(cajero)

    assert client.get(reverse("billing:invoice_pdf", args=[factura.pk])).status_code == 200


def test_emitir_por_htmx_cierra_el_modal_y_avisa(client, facturador, finalizada):
    client.force_login(facturador)
    url = reverse("billing:invoice_issue", args=[finalizada.pk])
    serie = InvoiceSeries.objects.get(code="f")

    vista_previa = client.get(url, headers={"hx-request": "true"})
    respuesta = client.post(url, {"series": serie.pk}, headers={"hx-request": "true"})

    assert "Emitir factura" in vista_previa.content.decode()
    assert respuesta.status_code == 200
    assert respuesta.content == b""
    assert "reserva:actualizada" in respuesta.headers["HX-Trigger"]
    assert Invoice.objects.filter(reservation=finalizada).count() == 1


def test_emitir_una_reserva_en_curso_devuelve_el_motivo(client, facturador, reserva):
    client.force_login(facturador)
    serie = InvoiceSeries.objects.get(code="f")

    respuesta = client.post(
        reverse("billing:invoice_issue", args=[reserva.pk]),
        {"series": serie.pk},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 422
    assert "finalizadas" in respuesta.content.decode()


def test_sin_permiso_no_se_abre_el_modal_de_emitir(client, cajero, finalizada):
    client.force_login(cajero)

    respuesta = client.get(reverse("billing:invoice_issue", args=[finalizada.pk]))

    assert respuesta.status_code == 403


def test_rectificar_lleva_a_la_rectificativa(client, administrador, factura):
    client.force_login(administrador)
    serie = InvoiceSeries.objects.get(code="fr")

    respuesta = client.post(
        reverse("billing:invoice_rectify", args=[factura.pk]),
        {"reason": "Datos del cliente erroneos", "series": serie.pk},
        headers={"hx-request": "true"},
    )

    rectificativa = Invoice.objects.get(rectifies=factura)
    assert respuesta.headers["HX-Redirect"] == reverse(
        "billing:invoice_detail", args=[rectificativa.pk]
    )


def test_pendiente_de_facturar_ensena_solo_lo_finalizado(client, facturador, finalizada):
    client.force_login(facturador)

    contenido = client.get(reverse("billing:to_invoice")).content.decode()
    assert finalizada.number in contenido

    issue_invoice(reservation=finalizada, actor=facturador)
    contenido = client.get(reverse("billing:to_invoice")).content.decode()
    assert finalizada.number not in contenido


def test_el_listado_de_cobros_respeta_la_oficina(client, cajero, de_norte, reserva):
    from decimal import Decimal

    from apps.billing.models import PaymentMethod
    from apps.billing.services import register_payment

    register_payment(
        reservation=reserva,
        amount=Decimal("20"),
        method=PaymentMethod.CASH,
        reference="TPV-7788",
        actor=cajero,
    )

    client.force_login(cajero)
    assert "TPV-7788" in client.get(reverse("billing:payment_list")).content.decode()
    client.force_login(de_norte)
    assert "TPV-7788" not in client.get(reverse("billing:payment_list")).content.decode()


def test_las_series_son_solo_de_administracion(client, facturador, administrador):
    client.force_login(facturador)
    assert client.get(reverse("billing:series_list")).status_code == 403
    assert client.get(reverse("billing:series_create")).status_code == 403

    client.force_login(administrador)
    assert client.get(reverse("billing:series_list")).status_code == 200


def test_crear_una_serie_desde_el_modal(client, administrador):
    client.force_login(administrador)

    respuesta = client.post(
        reverse("billing:series_create"),
        {
            "code": "Web",
            "name": "Ventas web",
            "kind": "ordinary",
            "number_format": "W{sequence:04d}",
        },
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 200
    assert InvoiceSeries.objects.get(code="web").next_number == "W0001"


def test_un_formato_invalido_vuelve_al_formulario(client, administrador):
    client.force_login(administrador)

    respuesta = client.post(
        reverse("billing:series_create"),
        {"code": "mala", "name": "Mala", "kind": "ordinary", "number_format": "F{year}"},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 422
    assert "{sequence}" in respuesta.content.decode()


def test_el_menu_tiene_la_seccion_de_facturacion(client, administrador):
    client.force_login(administrador)

    contenido = client.get(reverse("core:home")).content.decode()

    for entrada in ("Pendiente de facturar", "Facturas", "Cobros", "Arqueo de caja", "Series"):
        assert entrada in contenido


def test_el_pdf_lleva_las_politicas_y_el_logo(
    client, facturador, finalizada, empresa, tmp_path, settings
):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from pypdf import PdfReader

    from apps.settings_app.models import Policy

    settings.MEDIA_ROOT = tmp_path
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    empresa.logo = SimpleUploadedFile("logo.png", png, content_type="image/png")
    empresa.save()
    Policy.objects.create(title="Fianza", body="Se devuelve al recibir el coche.")
    factura = issue_invoice(reservation=finalizada, actor=facturador)
    client.force_login(facturador)

    respuesta = client.get(reverse("billing:invoice_pdf", args=[factura.pk]))
    documento = PdfReader(io.BytesIO(respuesta.content))
    texto = documento.pages[0].extract_text()

    assert "Fianza" in texto
    assert "Se devuelve al recibir el coche." in texto
    assert documento.pages[0].images  # el logo va incrustado
