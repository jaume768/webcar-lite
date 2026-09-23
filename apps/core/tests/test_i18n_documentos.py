"""Los documentos del cliente salen traducidos en todos los idiomas.

El contrato, la factura y los correos se leen en el idioma del cliente. Si una
cadena se queda sin traducir, o `makemessages` la marca como dudosa porque el
texto en espanol cambio, Django imprime el original en espanol y el cliente
ingles o aleman recibe un documento a medias. Aqui se comprueba el .po, que es
donde empieza el fallo, y no el PDF, que tardaria diez veces mas.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

#: Plantillas que ve el cliente. Lo demas (mostrador, listados, botones) va
#: siempre en espanol y no necesita traduccion.
DOCUMENTOS_DEL_CLIENTE = (
    "apps/contracts/templates/contracts/contract.html",
    "apps/billing/templates/billing/invoice_pdf.html",
    "apps/notifications/templates/notifications/emails/",
)

IDIOMAS = ("en", "de", "fr")


def _traduccion(bloque: str) -> str:
    """El msgstr completo del bloque.

    Una traduccion larga se parte en varias lineas (`msgstr ""` y debajo los
    trozos entrecomillados): hay que juntarlas o una traduccion perfectamente
    escrita pareceria vacia.
    """
    lineas = bloque.split("\n")
    for indice, linea in enumerate(lineas):
        if not linea.startswith("msgstr "):
            continue
        trozos = [linea[len("msgstr ") :]]
        for siguiente in lineas[indice + 1 :]:
            if not siguiente.startswith('"'):
                break
            trozos.append(siguiente)
        return "".join(trozo.strip('"') for trozo in trozos)
    return ""


def _entradas(ruta: Path) -> list[dict]:
    """Bloques del .po con lo justo: referencias, marcas, msgid y msgstr."""
    entradas = []
    for bloque in ruta.read_text(encoding="utf-8").split("\n\n"):
        msgid = re.search(r'^msgid "(.*)"$', bloque, re.M)
        if not msgid or msgid.group(1) == "":
            continue  # la cabecera del fichero
        entradas.append(
            {
                "msgid": msgid.group(1),
                "msgstr": _traduccion(bloque),
                "referencias": " ".join(re.findall(r"^#:.*$", bloque, re.M)),
                "dudosa": bool(re.search(r"^#,.*fuzzy", bloque, re.M)),
            }
        )
    return entradas


@pytest.mark.parametrize("idioma", IDIOMAS)
def test_los_documentos_del_cliente_estan_traducidos(idioma):
    ruta = Path(settings.BASE_DIR) / "locale" / idioma / "LC_MESSAGES" / "django.po"
    entradas = [
        entrada
        for entrada in _entradas(ruta)
        if any(plantilla in entrada["referencias"] for plantilla in DOCUMENTOS_DEL_CLIENTE)
    ]

    assert entradas, f"El .po de {idioma} no referencia ningun documento del cliente."

    sin_traducir = [e["msgid"] for e in entradas if not e["msgstr"]]
    dudosas = [e["msgid"] for e in entradas if e["dudosa"]]

    assert not sin_traducir, f"Sin traducir en {idioma}: {sin_traducir}"
    # Django ignora las dudosas: el cliente veria el texto en espanol.
    assert not dudosas, (
        f"Traducciones dudosas en {idioma}: {dudosas}. Revisalas y quita la marca `fuzzy`."
    )
