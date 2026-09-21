"""Peticiones HTTP salientes (SES.Hospedajes, Stripe, Brevo) con la stdlib.

Sin dependencias nuevas: con tres integraciones que solo hacen POST y leen la
respuesta, `urllib` basta. Siempre con timeout: una pasarela colgada no puede
dejar colgado al worker de Celery.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT_SEGUNDOS = 20


class HttpError(Exception):
    """No se ha podido hablar con el servicio (red, DNS, timeout)."""


@dataclass(frozen=True)
class Respuesta:
    status: int
    body: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.body or "null")


def request(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None):
    peticion = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT_SEGUNDOS) as respuesta:
            return Respuesta(respuesta.status, respuesta.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # Un 4xx/5xx trae cuerpo con el motivo: se devuelve para poder leerlo.
        return Respuesta(exc.code, exc.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(str(exc)) from exc


def post_json(url: str, payload, *, headers: dict | None = None) -> Respuesta:
    cabeceras = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **(headers or {}),
    }
    cuerpo = json.dumps(payload).encode("utf-8")
    return request("POST", url, data=cuerpo, headers=cabeceras)


def post_form(url: str, campos: dict | list, *, headers: dict | None = None) -> Respuesta:
    cabeceras = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    cuerpo = urllib.parse.urlencode(campos).encode("utf-8")
    return request("POST", url, data=cuerpo, headers=cabeceras)
