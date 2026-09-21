from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    label = "billing"
    verbose_name = _("Facturacion")

    def ready(self):
        """Tonos de los estados de factura y serie (ver apps/core/badges.py)."""
        from apps.core import badges

        badges.register("invoice_in_force", "success")
        badges.register("invoice_rectified", "warning")
        badges.register("invoice_rectifying", "danger")
        badges.register("series_ordinary", "accent")
        badges.register("series_rectifying", "warning")
        badges.register("series_default", "info")
