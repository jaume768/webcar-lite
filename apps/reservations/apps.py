from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReservationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reservations"
    label = "reservations"
    verbose_name = _("Reservas")

    def ready(self):
        from apps.fleet.signals import vehicle_retired

        from . import receivers

        vehicle_retired.connect(
            receivers.on_vehicle_retired, dispatch_uid="reservations_vehicle_retired"
        )
