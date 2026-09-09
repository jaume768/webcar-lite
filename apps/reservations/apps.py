from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReservationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reservations"
    label = "reservations"
    verbose_name = _("Reservas")
