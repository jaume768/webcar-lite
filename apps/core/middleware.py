"""Usuario actual accesible fuera de la request, sin pasarlo por diez capas.

Lo usan `UserStampedModel` para rellenar created_by/updated_by y, mas adelante,
el registro de auditoria. Se guarda en un `ContextVar`: cada request (hilo o
tarea async) ve el suyo y nunca el de otra. Un modulo global o un atributo de
clase filtrarian el usuario entre peticiones concurrentes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_user: ContextVar = ContextVar("current_user", default=None)
#: IP y navegador de la request en curso, para la auditoria.
_request_meta: ContextVar = ContextVar("request_meta", default=None)


def client_ip(request) -> str | None:
    """IP de quien hace la peticion. Detras del proxy manda X-Forwarded-For."""
    if request is None:
        return None
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return reenviada.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None


def get_request_meta() -> dict:
    """`{"ip": ..., "user_agent": ...}` de la request en curso, o vacio."""
    return _request_meta.get() or {}


def get_current_user():
    """Usuario autenticado de la request en curso, o None.

    Devuelve None tambien para usuarios anonimos: quien llama quiere un usuario
    al que atribuir un cambio, y un `AnonymousUser` no sirve como clave ajena.
    """
    user = _current_user.get()
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


@contextmanager
def current_user(user) -> Iterator[None]:
    """Fija el usuario actual en un bloque. Para tareas Celery, comandos y tests."""
    token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(token)


class CurrentUserMiddleware:
    """Publica request.user en el contextvar durante el ciclo de la request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_user.set(getattr(request, "user", None))
        token_meta = _request_meta.set(
            {
                "ip": client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            }
        )
        try:
            return self.get_response(request)
        finally:
            # Imprescindible: sin reset, un hilo reutilizado por el servidor
            # arrastraria el usuario de la peticion anterior.
            _current_user.reset(token)
            _request_meta.reset(token_meta)


class InterfaceLanguageMiddleware:
    """El panel de gestion siempre en el idioma de la instalacion.

    Sustituye a LocaleMiddleware a proposito: con varios idiomas declarados
    (los del cliente), el panel cambiaria segun el navegador de cada empleado
    y quedaria a medio traducir. Lo que ve el cliente (correos, documentos,
    pagina de pago) activa su idioma explicitamente.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.utils import translation

        translation.activate(settings.LANGUAGE_CODE)
        request.LANGUAGE_CODE = settings.LANGUAGE_CODE
        try:
            return self.get_response(request)
        finally:
            translation.deactivate()
