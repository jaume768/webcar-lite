from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.customers"
    label = "customers"
    verbose_name = _("Clientes")

    def ready(self):
        """Tono del aviso de cliente conflictivo (ver apps/core/badges.py)."""
        from apps.core import badges

        badges.register("blacklisted", "danger")
