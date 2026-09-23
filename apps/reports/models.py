"""Los informes no tienen datos propios: leen de reservas, flota y facturacion.

Lo unico que vive aqui es el permiso de area, que necesita un modelo del que
colgar. Es el mismo patron que `billing.BillingPermissions`.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class ReportPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    """Ancla del permiso de informes. No crea tabla (`managed = False`)."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de informes")
        permissions = [
            ("view_reports", _("Puede consultar los informes y exportar para la gestoria")),
        ]
