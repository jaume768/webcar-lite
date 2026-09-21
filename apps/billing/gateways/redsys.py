"""Redsys (TPV virtual de los bancos espanoles), firma HMAC-SHA256.

El cliente va a la pagina del banco con un formulario firmado; el banco avisa
del resultado a nuestra URL de notificacion con otra firma, que es lo unico
que se cree. La fianza se pide como preautorizacion (tipo 1) y luego se
confirma (tipo 2) o se anula (tipo 9) por la API REST.
"""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings

from apps.core.http import HttpError, post_json

from . import GatewayError

URLS = {
    True: {
        "pago": "https://sis-t.redsys.es:25443/sis/realizarPago",
        "rest": "https://sis-t.redsys.es:25443/sis/rest/trataPeticionREST",
    },
    False: {
        "pago": "https://sis.redsys.es/sis/realizarPago",
        "rest": "https://sis.redsys.es/sis/rest/trataPeticionREST",
    },
}

PAGO = "0"
PREAUTORIZACION = "1"
CONFIRMAR_PREAUTORIZACION = "2"
ANULAR_PREAUTORIZACION = "9"

IDIOMA = {"es": "001", "en": "002", "fr": "004", "de": "005"}
EURO = "978"


def _clave_de_pedido(pedido: str, secreto_b64: str) -> bytes:
    """La clave del comercio cifrada con 3DES sobre el numero de pedido."""
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:  # versiones antiguas de cryptography
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES

    datos = pedido.encode()
    datos += b"\0" * (-len(datos) % 8)
    cifrador = Cipher(TripleDES(base64.b64decode(secreto_b64)), modes.CBC(b"\0" * 8)).encryptor()
    return cifrador.update(datos) + cifrador.finalize()


def sign(parametros_b64: str, pedido: str, *, secret: str | None = None) -> str:
    secreto = secret if secret is not None else settings.REDSYS_SECRET_KEY
    clave = _clave_de_pedido(pedido, secreto)
    return base64.b64encode(
        hmac.new(clave, parametros_b64.encode(), hashlib.sha256).digest()
    ).decode()


def encode_parameters(parametros: dict) -> str:
    return base64.b64encode(json.dumps(parametros).encode()).decode()


def order_number(pk: int, marca: int) -> str:
    """Numero de pedido de 12 cifras, unico por intento (Redsys no admite repetirlo).

    Las 4 primeras tienen que ser numericas: aqui lo son todas.
    """
    return f"{pk % 10**4:04d}{marca % 10**8:08d}"


def _parametros(*, pedido: str, amount: Decimal, tipo: str) -> dict:
    if not (settings.REDSYS_MERCHANT_CODE and settings.REDSYS_SECRET_KEY):
        raise GatewayError("Redsys no esta configurado (REDSYS_MERCHANT_CODE y REDSYS_SECRET_KEY).")
    return {
        "DS_MERCHANT_AMOUNT": str(int((Decimal(amount) * 100).quantize(Decimal("1")))),
        "DS_MERCHANT_ORDER": pedido,
        "DS_MERCHANT_MERCHANTCODE": settings.REDSYS_MERCHANT_CODE,
        "DS_MERCHANT_CURRENCY": EURO,
        "DS_MERCHANT_TRANSACTIONTYPE": tipo,
        "DS_MERCHANT_TERMINAL": settings.REDSYS_TERMINAL,
    }


def payment_form(
    *,
    pedido: str,
    amount: Decimal,
    description: str,
    notify_url: str,
    ok_url: str,
    ko_url: str,
    locale: str = "es",
    hold: bool = False,
) -> dict:
    """Lo que la pagina de pago envia al banco: `{"action", "fields"}`."""
    parametros = _parametros(pedido=pedido, amount=amount, tipo=PREAUTORIZACION if hold else PAGO)
    parametros.update(
        {
            "DS_MERCHANT_MERCHANTURL": notify_url,
            "DS_MERCHANT_URLOK": ok_url,
            "DS_MERCHANT_URLKO": ko_url,
            "DS_MERCHANT_PRODUCTDESCRIPTION": description[:125],
            "DS_MERCHANT_CONSUMERLANGUAGE": IDIOMA.get(locale, "001"),
        }
    )
    codificados = encode_parameters(parametros)
    return {
        "action": URLS[settings.REDSYS_TEST]["pago"],
        "fields": {
            "Ds_SignatureVersion": "HMAC_SHA256_V1",
            "Ds_MerchantParameters": codificados,
            "Ds_Signature": sign(codificados, pedido),
        },
    }


def parse_notification(parametros_b64: str, firma: str, *, secret: str | None = None) -> dict:
    """Decodifica y verifica la notificacion del banco. Sin firma valida, no hay nada."""
    try:
        datos = json.loads(base64.b64decode(parametros_b64))
    except (ValueError, TypeError) as exc:
        raise GatewayError("Notificacion de Redsys ilegible.") from exc
    pedido = datos.get("Ds_Order") or datos.get("DS_ORDER") or ""
    esperada = sign(parametros_b64, pedido, secret=secret)
    # Redsys firma la notificacion en base64 "URL safe".
    normalizar = lambda valor: valor.replace("-", "+").replace("_", "/")  # noqa: E731
    if not hmac.compare_digest(normalizar(esperada), normalizar(firma or "")):
        raise GatewayError("Firma de Redsys no valida.")
    return datos


def is_authorized(datos: dict) -> bool:
    """Codigos 0000 a 0099: operacion autorizada."""
    try:
        return 0 <= int(datos.get("Ds_Response", "9999")) <= 99
    except ValueError:
        return False


def _rest(*, pedido: str, amount: Decimal, tipo: str) -> dict:
    codificados = encode_parameters(_parametros(pedido=pedido, amount=amount, tipo=tipo))
    try:
        respuesta = post_json(
            URLS[settings.REDSYS_TEST]["rest"],
            {
                "Ds_SignatureVersion": "HMAC_SHA256_V1",
                "Ds_MerchantParameters": codificados,
                "Ds_Signature": sign(codificados, pedido),
            },
        )
    except HttpError as exc:
        raise GatewayError(f"Sin conexion con Redsys: {exc}") from exc
    cuerpo = respuesta.json() or {}
    if not respuesta.ok or "errorCode" in cuerpo:
        raise GatewayError(f"Redsys: {cuerpo.get('errorCode') or respuesta.body[:300]}")
    datos = parse_notification(cuerpo["Ds_MerchantParameters"], cuerpo["Ds_Signature"])
    if not is_authorized(datos):
        raise GatewayError(f"Redsys ha rechazado la operacion ({datos.get('Ds_Response')}).")
    return datos


def capture(pedido: str, amount: Decimal) -> dict:
    return _rest(pedido=pedido, amount=amount, tipo=CONFIRMAR_PREAUTORIZACION)


def release(pedido: str, amount: Decimal) -> dict:
    return _rest(pedido=pedido, amount=amount, tipo=ANULAR_PREAUTORIZACION)
