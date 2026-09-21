from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operations"
    label = "operations"
    verbose_name = _("Operativa de mostrador")

    def ready(self):
        """Tonos de los estados de multa y mantenimiento (ver apps/core/badges.py)."""
        from apps.core import badges

        for estado, tono in {
            "received": "neutral",
            "matched": "info",
            "no_match": "danger",
            "identified": "accent",
            "charged": "success",
            "closed": "neutral",
        }.items():
            badges.register(estado, tono)
