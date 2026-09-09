from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OfficesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.offices"
    label = "offices"
    verbose_name = _("Oficinas")
