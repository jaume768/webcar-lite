"""Mixins de vista para permisos y scope de oficina."""

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin


class StaffPermissionRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Exige sesion y permiso, y responde 403 en vez de redirigir.

    Con `raise_exception` un usuario que ya ha entrado y no tiene permiso ve un
    403 claro; sin el, Django lo mandaria al login una y otra vez.
    """

    raise_exception = True


class OfficeScopedMixin:
    """Recorta el queryset de la vista a las oficinas del usuario.

    Un objeto de otra oficina da 404, nunca 403: un 403 confirmaria que la
    reserva existe, y eso ya es informacion que el usuario no deberia tener.
    """

    def get_queryset(self):
        return super().get_queryset().for_user(self.request.user)
