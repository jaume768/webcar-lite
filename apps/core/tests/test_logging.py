"""Regresion: los formatters de structlog deben recibir el renderer instanciado.

Con `"processor": "structlog.processors.JSONRenderer"` (ruta de importacion) el
logging no falla al configurarse, sino en cada llamada a logger.info(), y el
error solo se ve como un traceback suelto en la salida estandar.
"""

import io
import json
import logging

import structlog
from django.conf import settings


def _handler_con_formato(nombre, stream):
    formatter_cfg = dict(settings.LOGGING["formatters"][nombre])
    factory = formatter_cfg.pop("()")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(factory(**formatter_cfg))
    return handler


def _emitir(nombre, stream, capsys):
    logger_std = logging.getLogger("apps.core.tests.logging")
    logger_std.handlers = [_handler_con_formato(nombre, stream)]
    logger_std.propagate = False
    logger_std.setLevel(logging.INFO)

    structlog.get_logger("apps.core.tests.logging").info("reserva_creada", reservation_id=7)

    salida_estandar = capsys.readouterr()
    assert "--- Logging error ---" not in salida_estandar.err
    return stream.getvalue()


def test_formatter_json_emite_json_valido(capsys):
    salida = _emitir("json", io.StringIO(), capsys)

    evento = json.loads(salida.strip().splitlines()[-1])
    assert evento["event"] == "reserva_creada"
    assert evento["reservation_id"] == 7
    assert evento["level"] == "info"


def test_formatter_plain_emite_linea_legible(capsys):
    salida = _emitir("plain", io.StringIO(), capsys)

    assert "reserva_creada" in salida
    assert "reservation_id=7" in salida
