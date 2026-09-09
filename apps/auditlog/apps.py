from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auditlog"
    label = "auditlog"
    verbose_name = _("Auditoria")
