from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = _("Usuarios y permisos")

    def ready(self):
        from axes.signals import user_locked_out

        from . import signals

        user_locked_out.connect(signals.registrar_bloqueo, dispatch_uid="accounts_lockout")
