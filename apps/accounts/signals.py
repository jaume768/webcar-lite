"""Registro de accesos: quien entra, quien falla y a quien se bloquea.

Van al log y a la auditoria. django-axes guarda ademas cada intento en sus
propias tablas, que son las que deciden el bloqueo.
"""

import structlog
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

logger = structlog.get_logger("accounts.access")


def _ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def registrar_entrada(sender, request, user, **kwargs):
    from apps.auditlog import services as audit
    from apps.auditlog.models import AuditAction

    logger.info("login_correcto", user_id=user.pk, email=user.email, ip=_ip(request))
    audit.record(AuditAction.LOGIN, _("Acceso de %(email)s") % {"email": user.email}, actor=user)


@receiver(user_logged_out)
def registrar_salida(sender, request, user, **kwargs):
    if user is not None:
        logger.info("logout", user_id=user.pk, ip=_ip(request))


@receiver(user_login_failed)
def registrar_fallo(sender, credentials, request=None, **kwargs):
    # Nunca se registra la contrasena, ni siquiera su longitud.
    from apps.auditlog import services as audit
    from apps.auditlog.models import AuditAction

    email = str(credentials.get("username") or "")[:120]
    logger.warning("login_fallido", email=email, ip=_ip(request))
    audit.record(
        AuditAction.LOGIN_FAILED,
        _("Acceso fallido con %(email)s") % {"email": email or "—"},
        changes={"email": email},
    )


def registrar_bloqueo(sender, request, username=None, **kwargs):
    """Conectado a la senal de django-axes desde AppConfig.ready()."""
    logger.error("cuenta_bloqueada", email=username, ip=_ip(request))
