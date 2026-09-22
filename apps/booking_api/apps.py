from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BookingApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.booking_api"
    label = "booking_api"
    verbose_name = _("API de reservas")
