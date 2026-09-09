from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fleet"
    label = "fleet"
    verbose_name = _("Flota")
