from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fleet"
    label = "fleet"
    verbose_name = _("Flota")

    def ready(self):
        """Registra el color de cada situacion de vehiculo.

        El tono es presentacion, no dominio: por eso vive en core.badges y cada
        app declara el suyo al arrancar (ver apps/core/badges.py).
        """
        from apps.core import badges

        from .models import VehicleStatus

        tonos = {
            VehicleStatus.AVAILABLE: "success",
            VehicleStatus.RESERVED: "info",
            VehicleStatus.RENTED: "accent",
            VehicleStatus.WORKSHOP: "warning",
            VehicleStatus.CLEANING: "info",
            VehicleStatus.BLOCKED: "danger",
            VehicleStatus.RETIRED: "neutral",
        }
        for estado, tono in tonos.items():
            badges.register(estado.value, tono)
