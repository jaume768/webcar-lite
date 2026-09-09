"""Validador de DNI, NIE y pasaporte. No toca la base de datos."""

import pytest
from django.core.exceptions import ValidationError

from apps.customers.validators import (
    letra_de_control,
    validar_dni,
    validar_documento,
    validar_nie,
    validar_pasaporte,
)

# DNI reales por construccion: numero + su letra de control calculada.
DNI_VALIDOS = ["12345678Z", "00000000T", "99999999R", "11111111H", "53987654F"]
NIE_VALIDOS = ["X1234567L", "Y2345678Z", "Z1234567R", "X0000000T"]


@pytest.mark.parametrize("documento", DNI_VALIDOS)
def test_dni_valido(documento):
    assert validar_dni(documento) == documento


def test_dni_normaliza_espacios_guiones_y_minusculas():
    """En mostrador se teclea como sale: '12345678-z' es el mismo DNI."""
    assert validar_dni(" 12345678-z ") == "12345678Z"


def test_dni_con_letra_incorrecta():
    with pytest.raises(ValidationError) as error:
        validar_dni("12345678A")

    assert error.value.code == "dni_letra"
    assert "deberia ser Z" in str(error.value.messages[0])


@pytest.mark.parametrize("documento", ["1234567Z", "123456789Z", "12345678", "1234567AZ", ""])
def test_dni_con_longitud_o_forma_incorrecta(documento):
    with pytest.raises(ValidationError) as error:
        validar_dni(documento)

    assert error.value.code == "dni_formato"


@pytest.mark.parametrize("documento", NIE_VALIDOS)
def test_nie_valido(documento):
    assert validar_nie(documento) == documento


def test_nie_con_letra_incorrecta():
    with pytest.raises(ValidationError) as error:
        validar_nie("X1234567A")

    assert error.value.code == "nie_letra"


@pytest.mark.parametrize("documento", ["A1234567L", "X123456L", "X12345678L", "X1234567"])
def test_nie_con_forma_incorrecta(documento):
    with pytest.raises(ValidationError) as error:
        validar_nie(documento)

    assert error.value.code == "nie_formato"


def test_un_dni_valido_no_es_un_nie_valido():
    """La misma cadena cambia de resultado segun el tipo: por eso se valida con los dos datos."""
    assert validar_dni("12345678Z") == "12345678Z"
    with pytest.raises(ValidationError):
        validar_nie("12345678Z")


@pytest.mark.parametrize("documento", ["AB123456", "PASSPORT-99", "123456789012"])
def test_el_pasaporte_extranjero_se_acepta_sin_patron(documento):
    """Criterio de aceptacion: cada pais tiene su formato, no se rechaza ninguno."""
    esperado = documento.replace("-", "").upper()

    assert validar_pasaporte(documento) == esperado
    assert validar_documento("passport", documento) == esperado


@pytest.mark.parametrize("documento", ["AB12", "A" * 21])
def test_el_pasaporte_si_comprueba_la_longitud(documento):
    with pytest.raises(ValidationError) as error:
        validar_pasaporte(documento)

    assert error.value.code == "pasaporte_longitud"


def test_la_letra_de_control_sigue_la_tabla_oficial():
    assert letra_de_control(12345678) == "Z"
    assert letra_de_control(0) == "T"


def test_un_tipo_desconocido_no_pasa_por_alto():
    with pytest.raises(ValidationError) as error:
        validar_documento("carnet-de-la-biblioteca", "12345678Z")

    assert error.value.code == "tipo_desconocido"
