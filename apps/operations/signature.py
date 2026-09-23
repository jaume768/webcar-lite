"""Firma del cliente en la entrega.

En el mostrador el cliente firma con el dedo sobre la tablet y el navegador
manda el trazo como PNG en base64 (un `data:` URI). Esto no es un fichero que
suba el usuario: es un campo de texto que cualquiera podria escribir a mano, asi
que se comprueba en el servidor que de verdad es una imagen PNG pequena antes de
guardarla en el acta de entrega.

El modulo es puro a proposito: se prueba sin base de datos y sin cliente HTTP.
"""

import base64
import binascii

from django.utils.translation import gettext_lazy as _

#: Unico formato que acepta el lienzo (canvas.toDataURL()).
PREFIJO = "data:image/png;base64,"

#: Los ocho bytes con los que empieza todo PNG.
CABECERA_PNG = b"\x89PNG\r\n\x1a\n"

#: Una firma del lienzo pesa unos 10 KB. Se deja margen de sobra y se corta muy
#: por debajo de lo que costaria guardar cualquier otra cosa en este campo.
TAMANO_MAXIMO = 256 * 1024


class SignatureError(ValueError):
    """Lo recibido no es una firma valida."""


def clean_signature(valor: str) -> str:
    """Devuelve la firma normalizada, o cadena vacia si no hay firma.

    Levanta `SignatureError` si llega algo que no es un PNG en base64.
    """
    firma = (valor or "").strip()
    if not firma:
        return ""

    if not firma.startswith(PREFIJO):
        raise SignatureError(_("La firma no ha llegado bien. Pide al cliente que firme otra vez."))

    if len(firma) > TAMANO_MAXIMO:
        raise SignatureError(_("La firma ocupa demasiado. Borrala y vuelve a firmar."))

    datos = firma[len(PREFIJO) :]
    try:
        imagen = base64.b64decode(datos, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(
            _("La firma no ha llegado bien. Pide al cliente que firme otra vez.")
        ) from exc

    if not imagen.startswith(CABECERA_PNG):
        raise SignatureError(_("La firma no es una imagen valida."))

    return firma
