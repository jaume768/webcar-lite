from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auditlog"
    label = "auditlog"
    verbose_name = _("Auditoria")

    def ready(self):
        """Tonos de las acciones en el listado (ver apps/core/badges.py)."""
        from apps.core import badges

        for accion, tono in {
            "create": "success",
            "update": "info",
            "status": "accent",
            "cancel": "danger",
            "price": "warning",
            "payment": "success",
            "invoice": "accent",
            "login_failed": "danger",
            "compliance": "info",
            "online_payment": "success",
            "fine": "warning",
        }.items():
            badges.register(accion, tono)
