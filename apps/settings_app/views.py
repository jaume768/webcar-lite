"""Configuracion de la empresa, condiciones generales y politicas."""

import structlog
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from apps.core.crud import (
    CrudListView,
    CrudPermissionMixin,
    ModalCreateView,
    ModalUpdateView,
    ToggleActiveView,
)
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.tables import Column

from .filters import PolicyFilter
from .forms import CompanySettingsForm, PolicyForm, TermsVersionForm
from .models import CompanySettings, Policy, TermsVersion
from .services import save_policy, set_policy_active

logger = structlog.get_logger(__name__)

EVENTO = "configuracion:actualizada"


class SettingsView(CrudPermissionMixin, FormView):
    """Datos de la empresa y listado de versiones de las condiciones."""

    permission_required = "settings_app.access_settings"
    template_name = "settings_app/settings.html"
    form_class = CompanySettingsForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = CompanySettings.load()
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["page_title"] = _("Configuracion")
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Configuracion")},
        ]
        contexto["versiones"] = TermsVersion.objects.all()
        contexto["vigente"] = TermsVersion.current()
        return contexto

    def form_valid(self, form):
        form.save()
        logger.info("configuracion_de_empresa_guardada", actor_id=self.request.user.pk)
        messages.success(self.request, _("Datos de la empresa guardados."))
        return HttpResponseRedirect(reverse("settings_app:settings"))


class TermsCreateView(CrudPermissionMixin, FormView):
    """Redacta una version nueva de las condiciones."""

    permission_required = "settings_app.access_settings"
    template_name = "settings_app/_terms_modal.html"
    form_class = TermsVersionForm

    def get_initial(self):
        vigente = TermsVersion.current()
        if vigente is None:
            return {}
        # Se parte de la vigente: casi siempre es un retoque, no un texto nuevo.
        return {"title": vigente.title, "body": vigente.body}

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["form_action"] = self.request.path
        contexto["vigente"] = TermsVersion.current()
        return contexto

    def form_valid(self, form):
        version = form.save(commit=False)
        version.created_by = self.request.user
        version.save()
        version.publish(actor=self.request.user)

        logger.info(
            "condiciones_publicadas",
            version=version.version,
            actor_id=self.request.user.pk,
        )
        respuesta = HttpResponse(status=200)
        respuesta.headers["HX-Redirect"] = reverse("settings_app:settings")
        return trigger_toast(
            respuesta,
            _("Condiciones v%(version)s publicadas.") % {"version": version.version},
            "success",
        )

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class TermsPublishView(CrudPermissionMixin, View):
    """Vuelve a poner vigente una version anterior."""

    permission_required = "settings_app.access_settings"

    def post(self, request, pk, *args, **kwargs):
        version = get_object_or_404(TermsVersion, pk=pk)
        version.publish(actor=request.user)
        logger.warning("condiciones_revertidas", version=version.version, actor_id=request.user.pk)
        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO)
        respuesta.headers["HX-Redirect"] = reverse("settings_app:settings")
        return trigger_toast(
            respuesta,
            _("Ahora se aplican las condiciones v%(version)s.") % {"version": version.version},
            "success",
        )


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------


class PolicyListView(CrudListView):
    permission_required = "settings_app.view_policy"
    model = Policy
    filterset_class = PolicyFilter
    table_id = "tabla-politicas"
    table_row_template = "settings_app/_policy_row.html"
    table_columns = [
        Column(label=_("Orden"), css="tabular w-16"),
        Column(label=_("Política")),
        Column(label=_("En facturas")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Título o texto...")
    empty_title = _("Ninguna política")
    empty_message = _("Crea la primera: cancelación, combustible, fianza...")
    page_title = _("Políticas")
    create_url_name = "settings_app:policy_create"
    create_label = _("Nueva política")
    create_permission = "settings_app.add_policy"


class PolicyFormMixin:
    model = Policy
    form_class = PolicyForm
    table_id = "tabla-politicas"
    list_url_name = "settings_app:policy_list"
    modal_width = "max-w-2xl"

    def save_object(self, form):
        return save_policy(policy=form.save(commit=False), actor=self.request.user)


class PolicyCreateView(PolicyFormMixin, ModalCreateView):
    permission_required = "settings_app.add_policy"
    modal_title = _("Nueva política")
    submit_label = _("Crear política")
    success_message = _("Política %(objeto)s creada.")


class PolicyUpdateView(PolicyFormMixin, ModalUpdateView):
    permission_required = "settings_app.change_policy"
    modal_title = _("Editar política")
    success_message = _("Política %(objeto)s actualizada.")


class PolicyToggleView(ToggleActiveView):
    permission_required = "settings_app.change_policy"
    model = Policy
    list_url_name = "settings_app:policy_list"
    activated_message = _("Política %(objeto)s reactivada.")
    deactivated_message = _("Política %(objeto)s desactivada.")

    def perform(self, objeto):
        set_policy_active(policy=objeto, active=self.activate, actor=self.request.user)


class PolicyActivateView(PolicyToggleView):
    activate = True


class PolicyDeactivateView(PolicyToggleView):
    activate = False
