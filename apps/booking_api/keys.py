"""Claves de la API: se generan, se ensenan una vez y solo se guarda su huella."""

import hashlib
import hmac
import secrets

#: Delante de cada clave, para reconocerla a simple vista en un log o un .env.
MARCA = "rfk"
LARGO_PREFIJO = 12


def fingerprint(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def generate() -> tuple[str, str, str]:
    """Devuelve (clave, prefijo, huella). La clave no se vuelve a poder leer."""
    prefijo = secrets.token_hex(LARGO_PREFIJO // 2)
    clave = f"{MARCA}_{prefijo}_{secrets.token_urlsafe(32)}"
    return clave, prefijo, fingerprint(clave)


def prefix_of(clave: str) -> str:
    partes = (clave or "").split("_", 2)
    if len(partes) != 3 or partes[0] != MARCA or len(partes[1]) != LARGO_PREFIJO:
        return ""
    return partes[1]


def matches(clave: str, huella: str) -> bool:
    return hmac.compare_digest(fingerprint(clave), huella)
