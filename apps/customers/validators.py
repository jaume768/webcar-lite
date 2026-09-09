"""Validacion de documentos de identidad espanoles.

DNI y NIE llevan letra de control calculada: un digito mal tecleado se detecta
aqui y no dos semanas despues, cuando la factura ya esta emitida y el dato
enviado a SES.Hospedajes. El pasaporte **no** se valida por patron: es un
documento extranjero y cada pais tiene el suyo; exigir un formato concreto
significaria rechazar clientes reales en el mostrador.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.text import normalizar_documento

#: Letra de control del DNI: resto de dividir el numero entre 23.
LETRAS_CONTROL = "TRWAGMYFPDXBNJZSQVHLCKE"

#: El NIE cambia la letra inicial por un digito antes de calcular la letra.
PREFIJOS_NIE = {"X": "0", "Y": "1", "Z": "2"}

PATRON_DNI = re.compile(r"^(\d{8})([A-Z])$")
PATRON_NIE = re.compile(r"^([XYZ])(\d{7})([A-Z])$")


def letra_de_control(numero: int) -> str:
    return LETRAS_CONTROL[numero % 23]


def validar_dni(valor: str) -> str:
    """Devuelve el DNI normalizado o lanza ValidationError."""
    documento = normalizar_documento(valor)
    coincidencia = PATRON_DNI.match(documento)
    if not coincidencia:
        raise ValidationError(
            _("Un DNI son 8 numeros y una letra, por ejemplo 12345678Z."),
            code="dni_formato",
        )

    numero, letra = coincidencia.groups()
    esperada = letra_de_control(int(numero))
    if letra != esperada:
        raise ValidationError(
            _("La letra del DNI no cuadra: para %(numero)s deberia ser %(letra)s.")
            % {"numero": numero, "letra": esperada},
            code="dni_letra",
        )
    return documento


def validar_nie(valor: str) -> str:
    """Devuelve el NIE normalizado o lanza ValidationError."""
    documento = normalizar_documento(valor)
    coincidencia = PATRON_NIE.match(documento)
    if not coincidencia:
        raise ValidationError(
            _("Un NIE empieza por X, Y o Z, sigue con 7 numeros y acaba en letra (X1234567L)."),
            code="nie_formato",
        )

    prefijo, numero, letra = coincidencia.groups()
    esperada = letra_de_control(int(PREFIJOS_NIE[prefijo] + numero))
    if letra != esperada:
        raise ValidationError(
            _("La letra del NIE no cuadra: para %(documento)s deberia ser %(letra)s.")
            % {"documento": prefijo + numero, "letra": esperada},
            code="nie_letra",
        )
    return documento


def validar_pasaporte(valor: str) -> str:
    """El pasaporte solo se normaliza.

    No hay patron que valga para todos los paises. Se comprueba unicamente que
    tenga una longitud razonable: el resto lo mira quien lo tiene delante.
    """
    documento = normalizar_documento(valor)
    if not 5 <= len(documento) <= 20:
        raise ValidationError(
            _("El numero de pasaporte tiene entre 5 y 20 caracteres."),
            code="pasaporte_longitud",
        )
    return documento


#: Validador por tipo de documento. La clave es `Customer.DocumentType`.
VALIDADORES = {
    "dni": validar_dni,
    "nie": validar_nie,
    "passport": validar_pasaporte,
}


def validar_documento(tipo: str, numero: str) -> str:
    """Normaliza y valida un documento segun su tipo."""
    validador = VALIDADORES.get(tipo)
    if validador is None:
        raise ValidationError(_("Tipo de documento desconocido."), code="tipo_desconocido")
    return validador(numero)
