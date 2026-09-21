"""Huella y QR de Verifactu (RD 1007/2023 y Orden HAC/1177/2024).

Funciones puras: reciben datos y devuelven cadenas. Ni base de datos ni
settings, para poder probarlas contra los ejemplos de la AEAT sin montar nada.

Cada registro de factura lleva la huella del anterior dentro de la suya, asi que
la cadena entera se rompe si alguien toca una factura antigua. Por eso se
calcula al emitir, en la misma transaccion, y no despues.
"""

import hashlib
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

#: Servicio de cotejo de la AEAT al que apunta el QR impreso en la factura.
URL_COTEJO = "https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR"


def importe(valor: Decimal) -> str:
    """Importe tal como va en el registro: punto decimal y dos decimales."""
    return str(Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fecha(valor: date) -> str:
    """Fecha de expedicion en el formato de la AEAT: dd-mm-aaaa."""
    return valor.strftime("%d-%m-%Y")


def marca_de_tiempo(valor: datetime) -> str:
    """Fecha, hora y huso del registro, al segundo (ISO 8601 con desfase)."""
    if valor.tzinfo is None:
        raise ValueError("La fecha del registro tiene que llevar zona horaria.")
    return valor.replace(microsecond=0).isoformat()


def huella_de_alta(
    *,
    nif_emisor: str,
    numero: str,
    fecha_expedicion: date,
    tipo_factura: str,
    cuota_total: Decimal,
    importe_total: Decimal,
    huella_anterior: str,
    generado_en: datetime,
) -> str:
    """SHA-256 del registro de alta, en hexadecimal y mayusculas.

    El orden y los nombres de los campos son los de la especificacion: si
    cambia uno solo, la AEAT calcula otra huella y la cadena no cuadra.
    """
    campos = [
        ("IDEmisorFactura", nif_emisor.strip()),
        ("NumSerieFactura", numero.strip()),
        ("FechaExpedicionFactura", fecha(fecha_expedicion)),
        ("TipoFactura", tipo_factura),
        ("CuotaTotal", importe(cuota_total)),
        ("ImporteTotal", importe(importe_total)),
        ("Huella", huella_anterior),
        ("FechaHoraHusoGenRegistro", marca_de_tiempo(generado_en)),
    ]
    cadena = "&".join(f"{nombre}={valor}" for nombre, valor in campos)
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest().upper()


def url_qr(*, nif_emisor: str, numero: str, fecha_expedicion: date, importe_total: Decimal) -> str:
    """URL que codifica el QR: con ella cualquiera coteja la factura en la AEAT."""
    parametros = urlencode(
        {
            "nif": nif_emisor.strip(),
            "numserie": numero.strip(),
            "fecha": fecha(fecha_expedicion),
            "importe": importe(importe_total),
        }
    )
    return f"{URL_COTEJO}?{parametros}"


def qr_svg(datos: str) -> str:
    """El QR como SVG en linea, listo para la ficha y para el PDF."""
    import segno

    return segno.make(datos, error="m").svg_inline(scale=3, border=2, dark="#172054")
