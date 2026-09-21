"""El contrato sale en el idioma del cliente; las condiciones, tal cual."""

import pytest

pytestmark = pytest.mark.django_db


def _texto(contrato) -> str:
    from pypdf import PdfReader

    with contrato.file.open("rb") as fichero:
        return "\n".join(p.extract_text() or "" for p in PdfReader(fichero).pages)


def test_el_contrato_sale_en_ingles_para_un_cliente_ingles(
    reserva, empresa, condiciones, empleado, emitir
):
    reserva.customer.language = "en"
    reserva.customer.save(update_fields=["language"])

    texto = _texto(emitir(reserva, empleado))

    assert "Rental agreement" in texto
    # Los titulos de bloque van en mayusculas por CSS.
    assert "renter" in texto.lower()
    assert "Full to full" in texto  # politica de combustible
    assert "For the company" in texto
    assert "Por la empresa" not in texto
    # Las condiciones generales son texto de la empresa: no se traducen.
    assert "devolver el vehiculo en la fecha pactada" in texto
