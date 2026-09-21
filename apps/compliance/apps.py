from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compliance"
    label = "compliance"
    verbose_name = _("Cumplimiento normativo")

    def ready(self):
        from apps.core import badges

        for estado, tono in {
            "incomplete": "warning",
            "ready": "info",
            "sent": "success",
            "accepted": "success",
            "rejected": "danger",
            "error": "danger",
        }.items():
            badges.register(estado, tono)
