"""Huella y QR de Verifactu. Sin base de datos: son funciones puras."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from apps.billing.verifactu import URL_COTEJO, huella_de_alta, importe, qr_svg, url_qr

UNA_HORA = timezone(timedelta(hours=1))


def _huella(**cambios):
    datos = {
        "nif_emisor": "89890001K",
        "numero": "12345678/G33",
        "fecha_expedicion": date(2024, 1, 1),
        "tipo_factura": "F1",
        "cuota_total": Decimal("12.35"),
        "importe_total": Decimal("123.45"),
        "huella_anterior": "",
        "generado_en": datetime(2024, 1, 1, 19, 20, 30, tzinfo=UNA_HORA),
    }
    datos.update(cambios)
    return huella_de_alta(**datos)


def test_la_huella_coincide_con_el_ejemplo_de_la_aeat():
    """Primer registro de alta del documento de especificaciones de la AEAT.

    Si esta huella cambia, la AEAT calcularia otra distinta para la misma
    factura y la cadena entera dejaria de cuadrar.
    """
    assert _huella() == "3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60"


def test_la_huella_depende_de_la_anterior():
    """Es lo que encadena: tocar una factura vieja rompe todas las siguientes."""
    assert _huella(huella_anterior="A" * 64) != _huella()


def test_la_huella_ignora_los_microsegundos():
    con_microsegundos = datetime(2024, 1, 1, 19, 20, 30, 999_999, tzinfo=UNA_HORA)
    assert _huella(generado_en=con_microsegundos) == _huella()


def test_sin_zona_horaria_no_hay_huella():
    with pytest.raises(ValueError):
        _huella(generado_en=datetime(2024, 1, 1, 19, 20, 30))


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(Decimal("123.4"), "123.40"), (Decimal("0.005"), "0.01"), (Decimal("-50"), "-50.00")],
)
def test_los_importes_van_con_dos_decimales_y_punto(valor, esperado):
    assert importe(valor) == esperado


def test_el_qr_apunta_al_cotejo_de_la_aeat():
    url = url_qr(
        nif_emisor="B07456123",
        numero="F2026-00001",
        fecha_expedicion=date(2026, 9, 21),
        importe_total=Decimal("281.63"),
    )
    partes = urlparse(url)

    assert url.startswith(URL_COTEJO)
    assert parse_qs(partes.query) == {
        "nif": ["B07456123"],
        "numserie": ["F2026-00001"],
        "fecha": ["21-09-2026"],
        "importe": ["281.63"],
    }


def test_el_qr_se_pinta_como_svg():
    assert qr_svg("https://ejemplo.es").startswith("<svg")
