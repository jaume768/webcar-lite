"""Acceso al sistema y gestion de usuarios.

No hay alta publica: las cuentas las crea alguien con `accounts.manage_users`.
"""

import structlog
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required, permission_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import csrf
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.tables import Column, Filter, FilterOption, Table, paginate

from .forms import EmailAuthenticationForm, UserForm
from .mixins import StaffPermissionRequiredMixin
from .models import Role, User
from .services import UserServiceError, activate_user, create_user, deactivate_user, update_user

logger = structlog.get_logger(__name__)

publico = method_decorator(login_not_required, name="dispatch")


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


@publico
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        # El boton de demostracion solo existe con DEMO_MODE: en una
        # instalacion real no se ensena (y la vista responde 404).
        contexto["demo_activa"] = settings.DEMO_MODE
        return contexto


def csrf_failure(request, reason=""):
    """Fallo de CSRF: 403, salvo el doble envio del formulario de entrada.

    Al entrar, Django rota el token CSRF. Si el formulario sale dos veces
    (doble clic, Enter repetido), el segundo envio lleva el token viejo y
    fallaria con un 403 aunque el primero ya haya abierto la sesion. A quien ya
    esta dentro se le lleva a su panel; cualquier otro caso sigue siendo 403.
    """
    if request.user.is_authenticated and request.path == reverse("accounts:login"):
        logger.info("login_reenviado_con_sesion", user_id=request.user.pk)
        return HttpResponseRedirect(reverse("core:home"))
    return csrf.csrf_failure(request, reason=reason)


class LogoutView(auth_views.LogoutView):
    """Solo por POST: un GET permitiria cerrar la sesion desde un enlace ajeno."""


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")


class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


@publico
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


@publico
class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


@publico
class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


@publico
class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_not_required
def registration_disabled(request):
    """El alta publica no existe y no va a existir.

    La ruta se declara a proposito para que quede escrito: quien busque
    /registro/ recibe un 410, no un 404 ambiguo que invite a seguir probando.
    """
    logger.info("intento_de_registro_publico", path=request.path)
    return HttpResponse(
        _("El acceso a este sistema es privado. Las cuentas las crea un responsable."),
        status=410,
        content_type="text/plain; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Gestion de usuarios
# ---------------------------------------------------------------------------

TAMANO_PAGINA = 25


class UserListView(StaffPermissionRequiredMixin, ListView):
    permission_required = "accounts.manage_users"
    model = User
    template_name = "accounts/user_list.html"

    def get_queryset(self):
        consulta = User.objects.select_related("role").prefetch_related("offices")

        buscado = self.request.GET.get("q", "").strip()
        if buscado:
            consulta = consulta.filter(email__icontains=buscado) | consulta.filter(
                last_name__icontains=buscado
            )
        rol = self.request.GET.get("rol", "")
        if rol:
            consulta = consulta.filter(role__code=rol)
        estado = self.request.GET.get("estado", "")
        if estado:
            consulta = consulta.filter(is_active=estado == "activo")

        # Un usuario que solo gestiona su oficina no tiene por que ver la
        # plantilla entera de la empresa.
        if not self.request.user.is_superuser:
            consulta = consulta.filter(offices__in=self.request.user.offices.all())

        return consulta.distinct()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        peticion = self.request
        contexto["table"] = Table(
            id="tabla-usuarios",
            url=reverse("accounts:user_list"),
            columns=[
                Column(label=_("Usuario")),
                Column(label=_("Correo")),
                Column(label=_("Rol")),
                Column(label=_("Oficinas")),
                Column(label=_("Estado")),
                Column(label=_("Acciones"), align="right"),
            ],
            page_obj=paginate(peticion, self.get_queryset(), TAMANO_PAGINA),
            row_template="accounts/_user_row.html",
            search_value=peticion.GET.get("q", ""),
            search_placeholder=_("Nombre o correo..."),
            filters=[
                Filter(
                    name="rol",
                    label=_("Rol"),
                    value=peticion.GET.get("rol", ""),
                    options=[
                        FilterOption(value=r.code, label=r.name)
                        for r in Role.objects.order_by("name")
                    ],
                ),
                Filter(
                    name="estado",
                    label=_("Estado"),
                    value=peticion.GET.get("estado", ""),
                    options=[
                        FilterOption(value="activo", label=_("Activos")),
                        FilterOption(value="inactivo", label=_("Desactivados")),
                    ],
                ),
            ],
            empty_title=_("Ningun usuario coincide"),
            empty_message=_("Cambia la busqueda o quita algun filtro."),
        )
        contexto["page_title"] = _("Usuarios")
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Usuarios")},
        ]
        return contexto

    def render_to_response(self, context, **kwargs):
        if self.request.htmx:
            return render(self.request, "ui/_table.html#resultados", context)
        return super().render_to_response(context, **kwargs)


class UserFormMixin(StaffPermissionRequiredMixin):
    permission_required = "accounts.manage_users"
    model = User
    form_class = UserForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # El formulario recorta las oficinas asignables a las del actor.
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Usuarios"), "url": reverse("accounts:user_list")},
            {"label": contexto.get("page_title", "")},
        ]
        return contexto


class UserCreateView(UserFormMixin, CreateView):
    def get_context_data(self, **kwargs):
        kwargs.setdefault("page_title", _("Nuevo usuario"))
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        # El alta la hace el servicio, no el ModelForm: hay reglas (contrasena
        # inservible, registro del alta) que no son cosa del formulario.
        self.object = create_user(form_data=dict(form.cleaned_data), actor=self.request.user)
        usuario = self.object
        messages.success(
            self.request,
            _("Usuario %(email)s creado. Recibira un correo para poner su contrasena.")
            % {"email": usuario.email},
        )
        return HttpResponseRedirect(self.get_success_url())


class UserUpdateView(UserFormMixin, UpdateView):
    def get_queryset(self):
        consulta = User.objects.all()
        if not self.request.user.is_superuser:
            # Fuera de su alcance, el usuario ni siquiera existe.
            consulta = consulta.filter(offices__in=self.request.user.offices.all())
        return consulta.distinct()

    def get_context_data(self, **kwargs):
        kwargs.setdefault("page_title", _("Editar usuario"))
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        update_user(user=self.object, form_data=dict(form.cleaned_data), actor=self.request.user)
        messages.success(self.request, _("Usuario actualizado."))
        return HttpResponseRedirect(self.get_success_url())


def _usuario_gestionable(request, pk) -> User:
    consulta = User.objects.all()
    if not request.user.is_superuser:
        consulta = consulta.filter(offices__in=request.user.offices.all())
    return get_object_or_404(consulta.distinct(), pk=pk)


@require_POST
@permission_required("accounts.manage_users", raise_exception=True)
def user_deactivate(request, pk):
    """Baja logica. Un usuario nunca se borra: la auditoria le apunta."""
    usuario = _usuario_gestionable(request, pk)
    try:
        deactivate_user(user=usuario, actor=request.user)
    except UserServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Usuario %(email)s desactivado.") % {"email": usuario.email})
    return HttpResponseRedirect(reverse("accounts:user_list"))


@require_POST
@permission_required("accounts.manage_users", raise_exception=True)
def user_activate(request, pk):
    usuario = _usuario_gestionable(request, pk)
    activate_user(user=usuario, actor=request.user)
    messages.success(request, _("Usuario %(email)s reactivado.") % {"email": usuario.email})
    return HttpResponseRedirect(reverse("accounts:user_list"))
