from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SettingsAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settings_app"
    label = "settings_app"
    verbose_name = _("Configuracion")
