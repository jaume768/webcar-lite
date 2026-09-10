"""Generacion del contrato y acceso a los documentos."""

import pytest
from django.urls import reverse

from apps.contracts.models import Contract, ContractStatus
from apps.contracts.services import ContractServiceError, contract_context, request_contract
from apps.settings_app.models import TermsVersion

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Generacion
# ---------------------------------------------------------------------------


def test_el_contrato_se_genera_y_queda_listo(reserva, empresa, condiciones, empleado, emitir):
    """Con CELERY_TASK_ALWAYS_EAGER la tarea corre en el acto."""
    contrato = emitir(reserva, empleado)

    assert contrato.status == ContractStatus.READY
    assert contrato.file.name.endswith(".pdf")
    assert contrato.generated_at is not None
    assert contrato.generated_by == empleado


def test_el_pdf_es_un_pdf_de_verdad(reserva, empresa, condiciones, empleado, emitir):
    contrato = emitir(reserva, empleado)

    with contrato.file.open("rb") as fichero:
        contenido = fichero.read()

    assert contenido.startswith(b"%PDF-")
    assert len(contenido) > 5_000, "un contrato con datos pesa mas que una hoja vacia"


def test_el_pdf_lleva_datos_reales_y_no_marcadores(reserva, empresa, condiciones, empleado, emitir):
    """Criterio de aceptacion: nada de placeholders."""
    from pypdf import PdfReader

    contrato = emitir(reserva, empleado)

    with contrato.file.open("rb") as fichero:
        lector = PdfReader(fichero)
        texto = "\n".join(pagina.extract_text() or "" for pagina in lector.pages)

    assert reserva.number in texto
    assert "Ana" in texto and "Garcia" in texto
    assert reserva.vehicle.plate in texto
    assert "Baleares Rent" in texto
    assert "B07123456" in texto
    assert "Palma Centro" in texto
    # Los importes se imprimen con coma decimal, que es como se leen aqui.
    assert str(reserva.deposit_amount).replace(".", ",") in texto
    assert "Lleno - lleno" in texto
    # Y las condiciones aplicadas, con su version.
    assert "condiciones" in texto.lower()
    assert "devolver el vehiculo en la fecha pactada" in texto


def test_el_pdf_cabe_en_a4(reserva, empresa, condiciones, empleado, emitir):
    """Criterio de aceptacion: A4 sin cortes."""
    from pypdf import PdfReader

    contrato = emitir(reserva, empleado)

    with contrato.file.open("rb") as fichero:
        lector = PdfReader(fichero)
        caja = lector.pages[0].mediabox

    # A4 en puntos: 595 x 842, con un punto de margen por el redondeo.
    assert abs(float(caja.width) - 595.28) < 1
    assert abs(float(caja.height) - 841.89) < 1
    assert len(lector.pages) >= 1


def test_sin_condiciones_publicadas_no_hay_contrato(reserva, empresa, empleado):
    assert TermsVersion.current() is None

    with pytest.raises(ContractServiceError) as fallo:
        request_contract(reservation=reserva, actor=empleado)

    assert "condiciones generales" in str(fallo.value)
    assert not Contract.objects.exists()


def test_sin_titular_tampoco(reserva, empresa, condiciones, empleado):
    reserva.customer = None
    reserva.save(update_fields=["customer"])

    with pytest.raises(ContractServiceError):
        request_contract(reservation=reserva, actor=empleado)


def test_el_contrato_guarda_que_version_de_condiciones_aplico(
    reserva, empresa, condiciones, empleado, emitir
):
    """Lo que se firmo tiene que poder leerse dentro de tres anos."""
    contrato = emitir(reserva, empleado)

    nueva = TermsVersion.objects.create(title="Condiciones", body="Otra cosa distinta.")
    nueva.publish()

    contrato.refresh_from_db()
    assert contrato.terms_version == condiciones
    assert contrato.terms_version.version < nueva.version
    assert "Otra cosa distinta" not in contrato.terms_version.body


def test_regenerar_crea_otro_contrato_sin_borrar_el_anterior(
    reserva, empresa, condiciones, empleado
):
    primero = request_contract(reservation=reserva, actor=empleado)

    segundo = request_contract(reservation=reserva, actor=empleado)

    assert Contract.objects.count() == 2
    assert primero.pk != segundo.pk


def test_el_contexto_no_deja_huecos(reserva, empresa, condiciones):
    contexto = contract_context(reserva, terms=condiciones)

    assert contexto["empresa"].legal_name
    assert contexto["cliente"] is not None
    assert contexto["vehiculo"] is not None
    assert contexto["lineas"], "el desglose de precio no puede venir vacio"
    assert contexto["condiciones"].clauses


# ---------------------------------------------------------------------------
# Acceso a los ficheros
# ---------------------------------------------------------------------------


def test_el_contrato_no_tiene_url_publica(reserva, empresa, condiciones, empleado, emitir):
    """El almacen privado revienta si alguien intenta publicarlo."""
    contrato = emitir(reserva, empleado)

    with pytest.raises(ValueError):
        contrato.file.url  # noqa: B018 - se comprueba justo que esto falle


def test_quien_trabaja_en_la_oficina_lo_descarga(
    client, reserva, empresa, condiciones, empleado, emitir
):
    contrato = emitir(reserva, empleado)
    client.force_login(empleado)

    respuesta = client.get(reverse("contracts:download", args=[reserva.pk, contrato.pk]))

    assert respuesta.status_code == 200
    assert respuesta["Content-Type"] == "application/pdf"
    assert b"".join(respuesta.streaming_content).startswith(b"%PDF-")


def test_otra_oficina_no_puede_descargarlo_ni_con_la_url(
    client, reserva, empresa, condiciones, empleado, ajeno, emitir
):
    """Criterio de aceptacion: la URL directa no vale de nada."""
    contrato = emitir(reserva, empleado)
    client.force_login(ajeno)

    respuesta = client.get(reverse("contracts:download", args=[reserva.pk, contrato.pk]))

    assert respuesta.status_code == 404


def test_sin_sesion_no_se_descarga(client, reserva, empresa, condiciones, empleado, emitir):
    contrato = emitir(reserva, empleado)

    respuesta = client.get(reverse("contracts:download", args=[reserva.pk, contrato.pk]))

    assert respuesta.status_code == 302
    assert reverse("accounts:login") in respuesta.headers["Location"]


def test_sin_permiso_de_lectura_tampoco(
    client, reserva, empresa, condiciones, empleado, palma, emitir
):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    pelado = UserFactory(
        email="pelado@ejemplo.es",
        role=RoleFactory(code="pelado-doc", name="Sin permisos"),
        offices=[palma],
    )
    contrato = emitir(reserva, empleado)
    client.force_login(pelado)

    assert (
        client.get(reverse("contracts:download", args=[reserva.pk, contrato.pk])).status_code == 403
    )


def test_un_contrato_de_otra_reserva_no_se_cuela(
    client, reserva, empresa, condiciones, empleado, palma
):
    """La URL lleva reserva y contrato: tienen que casar."""
    from apps.customers.tests.factories import CustomerFactory
    from apps.reservations.services import create_quick_reservation
    from apps.reservations.tests.factories import en

    otra = create_quick_reservation(
        category=reserva.category,
        pickup_office=palma,
        customer=CustomerFactory(),
        pickup_at=en(10),
        return_at=en(12),
        actor=empleado,
    )
    contrato = request_contract(reservation=reserva, actor=empleado)
    client.force_login(empleado)

    respuesta = client.get(reverse("contracts:download", args=[otra.pk, contrato.pk]))

    assert respuesta.status_code == 404


# ---------------------------------------------------------------------------
# Condiciones generales versionadas
# ---------------------------------------------------------------------------


def test_una_version_publicada_no_se_edita(condiciones):
    """Es lo que garantiza que un contrato de ayer siga diciendo lo de ayer."""
    from django.core.exceptions import ValidationError

    condiciones.body = "Texto cambiado por la puerta de atras."

    with pytest.raises(ValidationError):
        condiciones.save()

    condiciones.refresh_from_db()
    assert "devolver el vehiculo" in condiciones.body


def test_publicar_una_version_retira_la_anterior(condiciones):
    nueva = TermsVersion.objects.create(title="Condiciones", body="Version dos.")

    nueva.publish()

    condiciones.refresh_from_db()
    assert nueva.is_current
    assert not condiciones.is_current
    assert TermsVersion.objects.filter(is_current=True).count() == 1


def test_las_versiones_se_numeran_solas(db):
    primera = TermsVersion.objects.create(title="C", body="Uno.")
    segunda = TermsVersion.objects.create(title="C", body="Dos.")

    assert primera.version == 1
    assert segunda.version == 2


def test_las_clausulas_se_parten_por_lineas(condiciones):
    assert len(condiciones.clauses) == 4
    assert condiciones.clauses[0].startswith("El arrendatario")


# ---------------------------------------------------------------------------
# Pestana de documentos
# ---------------------------------------------------------------------------


def test_la_pestana_lista_el_contrato(client, reserva, empresa, condiciones, empleado, emitir):
    from django.urls import reverse

    emitir(reserva, empleado)
    client.force_login(empleado)

    contenido = client.get(
        reverse("reservations:tab", args=[reserva.pk, "documentos"])
    ).content.decode()

    assert "Contrato de alquiler" in contenido
    assert f"condiciones v{condiciones.version}" in contenido
    assert "Descargar" in contenido


def test_la_pestana_de_otra_oficina_no_se_abre(client, reserva, empleado, ajeno):
    from django.urls import reverse

    client.force_login(ajeno)

    assert (
        client.get(reverse("reservations:tab", args=[reserva.pk, "documentos"])).status_code == 404
    )


def test_generar_el_contrato_desde_la_pantalla(
    client, reserva, empresa, condiciones, empleado, django_capture_on_commit_callbacks
):
    from django.urls import reverse

    client.force_login(empleado)

    with django_capture_on_commit_callbacks(execute=True):
        respuesta = client.post(reverse("contracts:create", args=[reserva.pk]))

    assert respuesta.status_code == 200
    contrato = Contract.objects.get()
    assert contrato.status == ContractStatus.READY


def test_sin_condiciones_la_pantalla_avisa_en_vez_de_reventar(client, reserva, empresa, empleado):
    from django.urls import reverse

    client.force_login(empleado)

    respuesta = client.post(reverse("contracts:create", args=[reserva.pk]))

    assert respuesta.status_code == 200
    assert "condiciones generales" in respuesta.headers["HX-Trigger"]
    assert not Contract.objects.exists()
