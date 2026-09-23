from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
    verbose_name = _("Nucleo")

    def ready(self):
        from . import badges

        # Tonos de los contactos de la web: lo nuevo llama la atencion, lo
        # ganado se ve verde y lo descartado se apaga.
        badges.register("lead_new", "warning")
        badges.register("lead_contacted", "info")
        badges.register("lead_won", "success")
        badges.register("lead_discarded", "neutral")
