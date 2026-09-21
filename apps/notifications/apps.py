from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    """Correos al cliente: confirmacion, recordatorio, contrato, devolucion, pagos.

    App propia porque es transversal (la disparan reservas, contratos,
    facturacion y pagos) y tiene su modelo: el registro de lo enviado.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = _("Notificaciones")

    def ready(self):
        from apps.core import badges

        tonos = {"queued": "info", "sent": "success", "failed": "danger", "skipped": "neutral"}
        for estado, tono in tonos.items():
            badges.register(f"email_{estado}", tono)
