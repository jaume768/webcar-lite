"""Registro de accesos.

La auditoria completa llega en su propio prompt. Aqui queda lo minimo que no
puede faltar desde el dia uno: quien entra, quien falla y a quien se bloquea.
django-axes guarda ademas cada intento en sus propias tablas.
"""

import structlog
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

logger = structlog.get_logger("accounts.access")


def _ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def registrar_entrada(sender, request, user, **kwargs):
    logger.info("login_correcto", user_id=user.pk, email=user.email, ip=_ip(request))


@receiver(user_logged_out)
def registrar_salida(sender, request, user, **kwargs):
    if user is not None:
        logger.info("logout", user_id=user.pk, ip=_ip(request))


@receiver(user_login_failed)
def registrar_fallo(sender, credentials, request=None, **kwargs):
    # Nunca se registra la contrasena, ni siquiera su longitud.
    logger.warning(
        "login_fallido",
        email=credentials.get("username"),
        ip=_ip(request),
    )


def registrar_bloqueo(sender, request, username=None, **kwargs):
    """Conectado a la senal de django-axes desde AppConfig.ready()."""
    logger.error("cuenta_bloqueada", email=username, ip=_ip(request))
