"""Firma del cliente en la entrega.

En el mostrador el cliente firma en la tablet. Lo que llega es texto, asi que se
comprueba en el servidor; y como a veces no se puede firmar en el momento (la
tablet sin bateria, el cliente con prisa), la entrega sale igual y queda
pendiente de firma.
"""

import base64
import zlib

import pytest

from apps.operations.models import FuelLevel
from apps.operations.services import OperationsServiceError, perform_check_in, sign_check_in
from apps.operations.signature import PREFIJO, SignatureError, clean_signature


def _png(ancho: int = 4, alto: int = 4) -> bytes:
    """Un PNG minimo de verdad, armado a mano para no depender de Pillow."""

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        cuerpo = tipo + datos
        return len(datos).to_bytes(4, "big") + cuerpo + zlib.crc32(cuerpo).to_bytes(4, "big")

    cabecera = ancho.to_bytes(4, "big") + alto.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    pixeles = zlib.compress(b"".join(b"\x00" + b"\xff" * ancho for _ in range(alto)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", cabecera)
        + trozo(b"IDAT", pixeles)
        + trozo(b"IEND", b"")
    )


def firma_de_prueba() -> str:
    return PREFIJO + base64.b64encode(_png()).decode()


# ---------------------------------------------------------------------------
# Validacion del trazo (sin base de datos)
# ---------------------------------------------------------------------------


def test_una_firma_del_lienzo_pasa():
    firma = firma_de_prueba()

    assert clean_signature(firma) == firma


def test_sin_firma_se_devuelve_vacio():
    assert clean_signature("") == ""
    assert clean_signature("   ") == ""


@pytest.mark.parametrize(
    "valor",
    [
        "<script>alert(1)</script>",
        "data:text/html;base64,aGVsbG8=",
        "data:image/png;base64,no-es-base64!!",
        # Base64 valido, pero lo que hay dentro no es una imagen.
        PREFIJO + base64.b64encode(b"esto no es un png").decode(),
    ],
)
def test_lo_que_no_es_un_png_se_rechaza(valor):
    with pytest.raises(SignatureError):
        clean_signature(valor)


def test_una_firma_enorme_se_rechaza():
    """El campo es texto libre: sin tope, cualquiera podria meter un fichero."""
    with pytest.raises(SignatureError):
        clean_signature(PREFIJO + "A" * (300 * 1024))


# ---------------------------------------------------------------------------
# Entrega firmada
# ---------------------------------------------------------------------------


def _entregar(reserva, empleado, **kwargs):
    kwargs.setdefault("mileage", 10_100)
    kwargs.setdefault("fuel_level", FuelLevel.FULL)
    kwargs.setdefault("licence_verified", True)
    kwargs.setdefault("id_verified", True)
    return perform_check_in(reservation=reserva, employee=empleado, **kwargs)


@pytest.mark.django_db
def test_la_entrega_guarda_la_firma_y_la_hora(reserva, empleado):
    firma = firma_de_prueba()

    entrega = _entregar(reserva, empleado, customer_signature=firma)

    assert entrega.customer_signature == firma
    assert entrega.signed_at == entrega.actual_datetime
    assert entrega.is_signed


@pytest.mark.django_db
def test_sin_firma_el_coche_sale_igual_pero_queda_pendiente(reserva, empleado):
    entrega = _entregar(reserva, empleado)

    assert entrega.pk is not None
    assert entrega.customer_signature == ""
    assert entrega.signed_at is None
    assert not entrega.is_signed


@pytest.mark.django_db
def test_una_firma_falsa_no_entrega_el_coche(reserva, empleado):
    with pytest.raises(OperationsServiceError):
        _entregar(reserva, empleado, customer_signature="data:text/html;base64,aGVsbG8=")

    assert not hasattr(reserva, "check_in")


@pytest.mark.django_db
def test_una_entrega_pendiente_se_firma_despues(reserva, empleado):
    entrega = _entregar(reserva, empleado)
    firma = firma_de_prueba()

    sign_check_in(check_in=entrega, signature=firma, employee=empleado)

    entrega.refresh_from_db()
    assert entrega.customer_signature == firma
    assert entrega.signed_at is not None


@pytest.mark.django_db
def test_una_firma_no_se_sustituye_por_otra(reserva, empleado):
    """El acta se firma una vez: si no, la firma no probaria nada."""
    entrega = _entregar(reserva, empleado, customer_signature=firma_de_prueba())

    with pytest.raises(OperationsServiceError) as fallo:
        sign_check_in(check_in=entrega, signature=firma_de_prueba(), employee=empleado)

    assert "ya esta firmada" in str(fallo.value)


@pytest.mark.django_db
def test_firmar_en_blanco_no_cuenta(reserva, empleado):
    entrega = _entregar(reserva, empleado)

    with pytest.raises(OperationsServiceError):
        sign_check_in(check_in=entrega, signature="", employee=empleado)

    entrega.refresh_from_db()
    assert not entrega.is_signed


@pytest.mark.django_db
def test_la_firma_queda_en_la_auditoria(reserva, empleado):
    from apps.auditlog.models import AuditAction, AuditLog

    entrega = _entregar(reserva, empleado)
    sign_check_in(check_in=entrega, signature=firma_de_prueba(), employee=empleado)

    apunte = AuditLog.objects.filter(action=AuditAction.CHECK_IN).order_by("-id").first()
    assert apunte.changes == {"signed": True}
    assert reserva.number in apunte.message


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_el_mostrador_firma_desde_la_reserva(client, reserva, empleado):
    from django.urls import reverse

    entrega = _entregar(reserva, empleado)
    client.force_login(empleado)
    url = reverse("operations:check_in_signature", args=[reserva.pk])

    assert client.get(url).status_code == 200

    respuesta = client.post(url, {"customer_signature": firma_de_prueba()})

    assert respuesta.status_code == 200
    entrega.refresh_from_db()
    assert entrega.is_signed


@pytest.mark.django_db
def test_no_se_firma_una_reserva_de_otra_oficina(client, reserva, aeropuerto):
    """El scope de oficina tambien vale para la firma."""
    from django.urls import reverse

    from apps.accounts.tests.factories import RoleFactory, UserFactory

    ajeno = UserFactory(
        email="otra@ejemplo.es",
        role=RoleFactory(
            code="mostrador-otro",
            name="Otro mostrador",
            permissions=["reservations.view_reservation", "reservations.change_reservation"],
        ),
        offices=[aeropuerto],
    )
    client.force_login(ajeno)

    respuesta = client.get(reverse("operations:check_in_signature", args=[reserva.pk]))

    assert respuesta.status_code == 404
