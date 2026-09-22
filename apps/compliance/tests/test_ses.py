"""SES.Hospedajes (RD 933/2021): el parte del contrato de alquiler."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from django.utils import timezone

from apps.compliance import services as ses_services
from apps.compliance import ses
from apps.compliance.models import SesStatus
from apps.core.http import HttpError, Respuesta

# ---------------------------------------------------------------------------
# Funciones puras: sin base de datos
# ---------------------------------------------------------------------------


def _oficina(**kwargs):
    datos = {
        "address": "Calle Mayor 1",
        "postal_code": "46001",
        "city": "Valencia",
        "province": "Valencia",
        "country": "ES",
    }
    return SimpleNamespace(**{**datos, **kwargs})


def _cliente(**kwargs):
    datos = {
        "first_name": "Lucía",
        "last_name": "Martín Pérez",
        "document_type": "dni",
        "document_number": "12345678Z",
        "document_support": "ABC123456",
        "birth_date": date(1990, 5, 1),
        "nationality": "ES",
        "sex": "female",
        "phone": "600111222",
        "email": "lucia@example.com",
        "licence_number": "12345678Z",
        "address": "Calle Luna 3",
        "postal_code": "46002",
        "city": "Valencia",
        "province": "Valencia",
        "country": "ES",
    }
    return SimpleNamespace(**{**datos, **kwargs})


def _reserva(**kwargs):
    entrega = timezone.now()
    datos = {
        "number": "R2026-00001",
        "vehicle": SimpleNamespace(plate="1234ABC", brand="Seat", model="Ibiza", color="Blanco"),
        "customer": _cliente(),
        "actual_pickup_at": entrega,
        "pickup_at": entrega,
        "return_at": entrega + timedelta(days=3),
        "pickup_office": _oficina(),
        "return_office": _oficina(),
    }
    return SimpleNamespace(**{**datos, **kwargs})


def test_un_parte_completo_no_pide_nada():
    datos = ses.build_payload(_reserva(), landlord_code="0000000001", payment_method="card")
    assert ses.missing_fields(datos) == []
    assert datos["contrato"]["tipo_pago"] == "TARJT"
    assert datos["personas"][0]["apellido1"] == "Martín"
    assert datos["personas"][0]["apellido2"] == "Pérez"


def test_sin_coche_ni_cobro_ni_codigo_de_arrendador():
    datos = ses.build_payload(_reserva(vehicle=None), landlord_code="", payment_method="")
    faltan = ses.missing_fields(datos)
    assert any("SES_LANDLORD_CODE" in f for f in faltan)
    assert "Medio de pago (registra el cobro o el anticipo)" in faltan
    assert "Matrícula del vehículo asignado" in faltan


def test_espanol_sin_segundo_apellido_y_dni_sin_soporte():
    cliente = _cliente(last_name="Martín", document_support="")
    datos = ses.build_payload(_reserva(customer=cliente), landlord_code="1", payment_method="cash")
    faltan = ses.missing_fields(datos)
    assert "Lucía Martín: segundo apellido" in faltan
    assert "Lucía Martín: número de soporte del documento" in faltan


def test_pasaporte_extranjero_no_pide_soporte_ni_segundo_apellido():
    cliente = _cliente(
        last_name="Smith",
        document_type="passport",
        document_number="X1234567",
        document_support="",
        nationality="GB",
    )
    datos = ses.build_payload(_reserva(customer=cliente), landlord_code="1", payment_method="cash")
    assert ses.missing_fields(datos) == []
    assert datos["personas"][0]["tipo_documento"] == "PAS"


def test_conductor_adicional_va_como_co():
    conductor = SimpleNamespace(
        first_name="Pedro",
        last_name="Ruiz Gil",
        document_number="87654321X",
        birth_date=date(1985, 1, 1),
        licence_country="ES",
        licence_number="87654321X",
    )
    datos = ses.build_payload(
        _reserva(), landlord_code="1", drivers=[conductor], payment_method="card"
    )
    assert [p["rol"] for p in datos["personas"]] == ["TI", "CO"]
    assert ses.missing_fields(datos) == []


def test_el_xml_se_lee_y_lleva_lo_esencial():
    datos = ses.build_payload(_reserva(), landlord_code="0000000001", payment_method="card")
    raiz = ET.fromstring(ses.to_xml(datos).split("\n", 1)[1])
    assert raiz.findtext("cabecera/codigoArrendador") == "0000000001"
    assert raiz.findtext("cabecera/tipoComunicacion") == "AV"
    assert raiz.findtext("solicitud/comunicacion/vehiculo/matricula") == "1234ABC"
    assert raiz.findtext("solicitud/comunicacion/persona/numeroDocumento") == "12345678Z"
    assert raiz.findtext("solicitud/comunicacion/contrato/pago/tipoPago") == "TARJT"


# ---------------------------------------------------------------------------
# Servicio: preparar y enviar
# ---------------------------------------------------------------------------


@pytest.fixture
def reserva_entregada(db):
    from apps.billing.services import register_payment
    from apps.customers.tests.factories import CustomerFactory
    from apps.fleet.tests.factories import VehicleCategoryFactory, VehicleFactory
    from apps.offices.tests.factories import OfficeFactory
    from apps.reservations.models import ReservationStatus
    from apps.reservations.tests.factories import ReservationFactory

    oficina = OfficeFactory(code="ses", name="SES", address="Calle Mayor 1", city="Valencia")
    categoria = VehicleCategoryFactory(code="ses-eco")
    coche = VehicleFactory(
        plate="1111SES", category=categoria, current_office=oficina, color="Blanco"
    )
    cliente = CustomerFactory(
        last_name="Martín Pérez",
        document_support="ABC123456",
        sex="female",
        address="Calle Luna 3",
        city="Valencia",
        licence_number="LIC1",
    )
    reserva = ReservationFactory(
        category=categoria,
        vehicle=coche,
        customer=cliente,
        pickup_office=oficina,
        return_office=oficina,
        status=ReservationStatus.IN_PROGRESS,
        actual_pickup_at=timezone.now(),
    )
    register_payment(reservation=reserva, amount=Decimal("50.00"), method="card", from_gateway=True)
    return reserva


@pytest.fixture
def ses_configurado(settings):
    settings.SES_LANDLORD_CODE = "0000000001"
    settings.SES_ENDPOINT = "https://ses.example/comunicacion"
    settings.SES_USER = "usuario"
    settings.SES_PASSWORD = "clave"


def test_sin_codigo_de_arrendador_queda_incompleto(reserva_entregada, settings):
    settings.SES_LANDLORD_CODE = ""
    parte = ses_services.prepare(reservation=reserva_entregada)
    assert parte.status == SesStatus.INCOMPLETE
    assert parte.deadline_at == reserva_entregada.actual_pickup_at + timedelta(hours=24)
    with pytest.raises(ses_services.SesError):
        ses_services.send(submission=parte)


def test_sin_envio_real_se_simula_y_no_se_da_por_enviado(reserva_entregada, ses_configurado):
    parte = ses_services.prepare(reservation=reserva_entregada)
    assert parte.status == SesStatus.READY, parte.missing

    parte = ses_services.send(submission=parte)

    assert parte.simulated
    assert parte.status == SesStatus.READY
    assert parte.sent_at is None
    assert "<matricula>1111SES</matricula>" in parte.xml


def test_envio_real_aceptado(reserva_entregada, ses_configurado, settings, monkeypatch):
    settings.SES_ENABLED = True
    enviados = []

    def post_json(url, payload, headers=None):
        enviados.append((url, payload, headers))
        return Respuesta(200, '{"codigo": "0", "lote": "L-42"}')

    monkeypatch.setattr(ses_services, "post_json", post_json)
    parte = ses_services.prepare(reservation=reserva_entregada)

    parte = ses_services.send(submission=parte)

    assert parte.status == SesStatus.SENT
    assert parte.lot_code == "L-42"
    assert parte.sent_at is not None
    _url, payload, cabeceras = enviados[0]
    assert payload["codigoArrendador"] == "0000000001"
    assert cabeceras["Authorization"].startswith("Basic ")
    # Enviado no se reenvia.
    assert ses_services.send(submission=parte).attempts == 1


def test_envio_real_rechazado(reserva_entregada, ses_configurado, settings, monkeypatch):
    settings.SES_ENABLED = True
    monkeypatch.setattr(
        ses_services, "post_json", lambda *a, **k: Respuesta(400, '{"codigo": "10"}')
    )
    parte = ses_services.send(submission=ses_services.prepare(reservation=reserva_entregada))
    assert parte.status == SesStatus.REJECTED


def test_red_caida_queda_en_error_para_reintentar(
    reserva_entregada, ses_configurado, settings, monkeypatch
):
    settings.SES_ENABLED = True

    def caida(*a, **k):
        raise HttpError("timeout")

    monkeypatch.setattr(ses_services, "post_json", caida)
    parte = ses_services.send(submission=ses_services.prepare(reservation=reserva_entregada))
    assert parte.status == SesStatus.ERROR
    assert parte.attempts == 1
