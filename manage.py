#!/usr/bin/env python
"""Utilidad de linea de comandos de Django."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "No se encuentra Django. Activa el entorno virtual o levanta el contenedor."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
