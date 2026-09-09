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
        try:
            return self.get_response(request)
        finally:
            # Imprescindible: sin reset, un hilo reutilizado por el servidor
            # arrastraria el usuario de la peticion anterior.
            _current_user.reset(token)
