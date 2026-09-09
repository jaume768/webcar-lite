"""Normalizacion de texto para busquedas.

En mostrador nadie teclea acentos con prisa: "gonzalez" tiene que encontrar a
"Gonzalez" y a "Gonzalez" con tilde. La normalizacion se hace **en Python**, al
guardar y al buscar, y el resultado se guarda en una columna aparte con indice
trigram. La alternativa (envolver la columna en `unaccent()` dentro de la
consulta) no puede indexarse sin declarar funciones propias en la base de datos,
y ademas dejaria la busqueda sin usar el indice justo cuando hay volumen.
"""

import unicodedata


def normalizar(texto: str | None) -> str:
    """Minusculas, sin acentos y sin espacios de sobra.

    'González  Pérez' -> 'gonzalez perez'
    """
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().split())


def normalizar_documento(valor: str | None) -> str:
    """Documentos sin espacios ni guiones y en mayusculas: '12345678-z' -> '12345678Z'."""
    if not valor:
        return ""
    return "".join(str(valor).split()).replace("-", "").replace(".", "").upper()
