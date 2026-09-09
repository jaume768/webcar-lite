"""Ajustes de tests: los de desarrollo mas la app auxiliar de modelos.

Los mixins de `apps.core.models` son abstractos y no se pueden probar sin un
modelo concreto. `apps.core.tests.testapp` aporta uno que solo existe durante
los tests: no lleva migraciones y sus tablas las crea el propio runner.
"""

from .dev import *  # noqa: F403
from .dev import INSTALLED_APPS

INSTALLED_APPS = [*INSTALLED_APPS, "apps.core.tests.testapp"]
