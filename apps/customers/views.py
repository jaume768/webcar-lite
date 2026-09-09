"""Pantallas de clientes.

Mismo patron CRUD que el resto de maestros (docs/patrones/crud.md), mas dos
cosas propias: la marca de cliente conflictivo y los documentos escaneados, que
se guardan en un almacen privado y se entregan por una vista con permisos.
"""

import structlog
from django.contrib import messages
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, View

from apps.core.crud import (
    EVENTO_GUARDADO,
    CrudListView,
    CrudPermissionMixin,
    ModalCreateView,
    ModalFormView,
    ModalUpdateView,
    ToggleActiveView,
)
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.tables import Column
from apps.offices.selectors import get_active_office

from .filters import CustomerFilter
from .forms import CustomerBlacklistForm, CustomerDocumentForm, CustomerForm
from .models import Customer, CustomerDocument
from .services import (
    delete_document,
    save_customer,
    save_document,
    set_blacklisted,
    set_customer_active,
)

logger = structlog.get_logger(__name__)


class CustomerListView(CrudListView):
    permission_required = "customers.view_customer"
    model = Customer
    filterset_class = CustomerFilter
    table_id = "tabla-clientes"
    table_row_template = "customers/_customer_row.html"
    table_columns = [
        Column(label=_("Cliente")),
        Column(label=_("Documento"), css="font-mono tabular w-40"),
        Column(label=_("Contacto")),
        Column(label=_("Edad"), align="right", css="tabular w-20"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Nombre, apellidos, documento, telefono o correo...")
    empty_title = _("Ningun cliente coincide")
    empty_message = _("Prueba con parte del apellido o del documento.")
    page_title = _("Clientes")
    create_url_name = "customers:customer_create"
    create_label = _("Nuevo cliente")
    create_permission = "customers.add_customer"

    def get_base_queryset(self):
        return super().get_base_queryset().select_related("office")


class CustomerDetailView(CrudPermissionMixin, DetailView):
    """Ficha completa: datos, marca de conflictivo y documentos adjuntos."""

    permission_required = "customers.view_customer"
    model = Customer
    template_name = "customers/customer_detail.html"
    context_object_name = "customer"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        cliente = self.object
        contexto["page_title"] = cliente.full_name
        contexto["documents"] = cliente.documents.all()
        contexto["document_form"] = CustomerDocumentForm()
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Clientes"), "url": reverse("customers:customer_list")},
            {"label": cliente.full_name},
        ]
        return contexto

    def render_to_response(self, context, **kwargs):
        # Tras subir o borrar un documento, la ficha solo devuelve ese bloque.
        if self.request.htmx:
            return render(self.request, "customers/customer_detail.html#documentos", context)
        return super().render_to_response(context, **kwargs)


class CustomerFormMixin:
    model = Customer
    form_class = CustomerForm
    table_id = "tabla-clientes"
    list_url_name = "customers:customer_list"

    def save_object(self, form):
        return save_customer(
            customer=form.save(commit=False),
            actor=self.request.user,
            office=get_active_office(self.request),
        )


class CustomerCreateView(CustomerFormMixin, ModalCreateView):
    permission_required = "customers.add_customer"
    modal_title = _("Nuevo cliente")
    submit_label = _("Crear cliente")
    success_message = _("Cliente %(objeto)s creado.")


class CustomerUpdateView(CustomerFormMixin, ModalUpdateView):
    permission_required = "customers.change_customer"
    modal_title = _("Editar cliente")
    success_message = _("Cliente %(objeto)s actualizado.")


class CustomerBlacklistView(ModalFormView):
    """Marca de cliente conflictivo, con su motivo."""

    permission_required = "customers.change_customer"
    form_class = CustomerBlacklistForm
    table_id = "tabla-clientes"
    list_url_name = "customers:customer_list"
    modal_title = _("Marca de cliente")
    submit_label = _("Guardar marca")
    success_message = _("Marca de %(objeto)s actualizada.")

    @property
    def customer(self):
        if not hasattr(self, "_customer"):
            self._customer = get_object_or_404(Customer.objects.all(), pk=self.kwargs["pk"])
        return self._customer

    def get_initial(self):
        return {
            "is_blacklisted": self.customer.is_blacklisted,
            "blacklist_reason": self.customer.blacklist_reason,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modal_title"] = _("Marca de %(cliente)s") % {"cliente": self.customer.full_name}
        return contexto

    def save_object(self, form):
        return set_blacklisted(
            customer=self.customer,
            blacklisted=form.cleaned_data["is_blacklisted"],
            reason=form.cleaned_data.get("blacklist_reason", ""),
            actor=self.request.user,
        )


class CustomerToggleView(ToggleActiveView):
    permission_required = "customers.change_customer"
    model = Customer
    list_url_name = "customers:customer_list"
    activated_message = _("Cliente %(objeto)s reactivado.")
    deactivated_message = _("Cliente %(objeto)s dado de baja.")

    def perform(self, objeto):
        set_customer_active(customer=objeto, active=self.activate, actor=self.request.user)


class CustomerActivateView(CustomerToggleView):
    activate = True


class CustomerDeactivateView(CustomerToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------


class CustomerDocumentCreateView(ModalCreateView):
    """Subida de un escaneo a la ficha del cliente."""

    permission_required = "customers.change_customer"
    model = CustomerDocument
    form_class = CustomerDocumentForm
    table_id = "documentos"
    modal_title = _("Adjuntar documento")
    submit_label = _("Subir")
    success_message = _("Documento adjuntado.")

    @property
    def customer(self):
        if not hasattr(self, "_customer"):
            self._customer = get_object_or_404(Customer.objects.all(), pk=self.kwargs["pk"])
        return self._customer

    def get_list_url(self) -> str:
        return reverse("customers:customer_detail", args=[self.customer.pk])

    def save_object(self, form):
        documento = form.save(commit=False)
        documento.customer = self.customer
        return save_document(document=documento, actor=self.request.user)


class CustomerDocumentDownloadView(CrudPermissionMixin, View):
    """Entrega un documento privado.

    Los ficheros viven fuera de MEDIA_ROOT y no tienen URL: la unica forma de
    leerlos es esta vista, que primero comprueba sesion y permiso. Adivinar la
    ruta no sirve de nada porque el servidor de estaticos no la sirve.
    """

    permission_required = "customers.view_customer"

    def get(self, request, pk, *args, **kwargs):
        documento = get_object_or_404(CustomerDocument.objects.select_related("customer"), pk=pk)
        logger.info(
            "documento_de_cliente_descargado",
            document_id=documento.pk,
            customer_id=documento.customer_id,
            user_id=request.user.pk,
        )
        return FileResponse(
            documento.file.open("rb"),
            as_attachment=True,
            filename=documento.original_name or documento.file.name.rsplit("/", 1)[-1],
        )


class CustomerDocumentDeleteView(CrudPermissionMixin, View):
    """Quita un documento y borra el fichero del disco."""

    permission_required = "customers.change_customer"

    def post(self, request, pk, *args, **kwargs):
        documento = get_object_or_404(CustomerDocument.objects.select_related("customer"), pk=pk)
        cliente = documento.customer
        delete_document(document=documento, actor=request.user)
        mensaje = _("Documento borrado.")

        if not request.htmx:
            messages.success(request, mensaje)
            return HttpResponseRedirect(reverse("customers:customer_detail", args=[cliente.pk]))

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, mensaje, "success")
