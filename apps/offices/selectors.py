"""Consultas de oficinas. Lo que puede ver cada usuario sale de aqui."""

from apps.core.offices import SESSION_ACTIVE_OFFICE_KEY, OfficeChoice

from .models import Office


def offices_for_user(user):
    """Oficinas activas sobre las que el usuario puede operar.

    Un superusuario ve todas. Un usuario sin sesion o desactivado, ninguna:
    devolver todas por descuido seria abrir el sistema entero.
    """
    if user is None or not user.is_authenticated or not user.is_active:
        return Office.objects.none()
    if user.is_superuser:
        return Office.objects.active()
    return user.offices.filter(is_active=True)


def office_choices_for_request(request) -> list[OfficeChoice]:
    """Adaptador para el selector de la barra superior (apps.core.offices)."""
    usuario = getattr(request, "user", None)
    return [
        OfficeChoice(id=str(office.pk), name=office.name)
        for office in offices_for_user(usuario).order_by("name")
    ]


def get_active_office(request) -> Office | None:
    """Oficina activa como objeto de dominio, ya validada contra el usuario.

    Nunca devuelve una oficina fuera del alcance del usuario, ni aunque la
    sesion arrastre un id antiguo.
    """
    permitidas = offices_for_user(getattr(request, "user", None))
    if not permitidas.exists():
        return None

    activa_id = request.session.get(SESSION_ACTIVE_OFFICE_KEY)
    if activa_id:
        oficina = permitidas.filter(pk=activa_id).first()
        if oficina is not None:
            return oficina
    return permitidas.order_by("name").first()
