"""Ancla de permisos de availability.

Los permisos de este dominio hacen falta ya (los roles los reparten), pero sus
modelos aun no existen. Este modelo no gestionado no crea tabla: solo da a
Django un content type de la app con el que registrar los permisos, de modo que
las comprobaciones se escriben desde el principio como `availability.<permiso>`.

Cuando lleguen los modelos reales, sus permisos se declaran en el Meta del
modelo que les corresponde y una migracion de datos mueve estas filas al nuevo
content type. Los codigos no cambian, asi que ni los roles ni las vistas se
enteran.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class AvailabilityPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de disponibilidad")
        permissions = [
            ("override_availability", _("Puede forzar una reserva sin disponibilidad")),
        ]
