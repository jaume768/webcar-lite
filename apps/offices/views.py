"""Pantallas de oficinas y grupos de oficinas.

Siguen el patron CRUD de `apps.core.crud` (documentado en
`docs/patrones/crud.md`): listado con django-filter, alta y edicion en modal
por HTMX, y baja logica. No hay vista de borrado y no es un olvido.
"""

from django.utils.translation import gettext_lazy as _

from apps.core.crud import CrudListView, ModalCreateView, ModalUpdateView, ToggleActiveView
from apps.core.tables import Column

from .filters import OfficeFilter, OfficePoolFilter
from .forms import OfficeForm, OfficePoolForm
from .models import Office, OfficePool
from .services import save_office, save_pool, set_office_active, set_pool_active

# ---------------------------------------------------------------------------
# Oficinas
# ---------------------------------------------------------------------------


class OfficeListView(CrudListView):
    permission_required = "offices.view_office"
    model = Office
    filterset_class = OfficeFilter
    table_id = "tabla-oficinas"
    table_row_template = "offices/_office_row.html"
    table_columns = [
        Column(label=_("Codigo"), css="font-mono tabular w-28"),
        Column(label=_("Nombre")),
        Column(label=_("Localidad")),
        Column(label=_("Grupo")),
        Column(label=_("Contacto")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo, nombre o localidad...")
    empty_title = _("Ninguna oficina coincide")
    empty_message = _("Cambia la busqueda o quita algun filtro.")
    page_title = _("Oficinas")
    create_url_name = "offices:office_create"
    create_label = _("Nueva oficina")
    create_permission = "offices.add_office"

    def get_base_queryset(self):
        return super().get_base_queryset().select_related("pool")


class OfficeFormMixin:
    model = Office
    form_class = OfficeForm
    table_id = "tabla-oficinas"
    list_url_name = "offices:office_list"

    def save_object(self, form):
        return save_office(office=form.save(commit=False), actor=self.request.user)


class OfficeCreateView(OfficeFormMixin, ModalCreateView):
    permission_required = "offices.add_office"
    modal_title = _("Nueva oficina")
    submit_label = _("Crear oficina")
    success_message = _("Oficina %(objeto)s creada.")


class OfficeUpdateView(OfficeFormMixin, ModalUpdateView):
    permission_required = "offices.change_office"
    modal_title = _("Editar oficina")
    success_message = _("Oficina %(objeto)s actualizada.")


class OfficeToggleView(ToggleActiveView):
    """La baja la decide el servicio: puede negarse y explicar por que."""

    permission_required = "offices.change_office"
    model = Office
    list_url_name = "offices:office_list"
    activated_message = _("Oficina %(objeto)s reactivada.")
    deactivated_message = _("Oficina %(objeto)s desactivada.")

    def perform(self, objeto):
        set_office_active(office=objeto, active=self.activate, actor=self.request.user)


class OfficeActivateView(OfficeToggleView):
    activate = True


class OfficeDeactivateView(OfficeToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Grupos de oficinas
# ---------------------------------------------------------------------------


class OfficePoolListView(CrudListView):
    permission_required = "offices.view_officepool"
    model = OfficePool
    filterset_class = OfficePoolFilter
    table_id = "tabla-grupos"
    table_row_template = "offices/_pool_row.html"
    table_columns = [
        Column(label=_("Codigo"), css="font-mono tabular w-28"),
        Column(label=_("Nombre")),
        Column(label=_("Oficinas")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ningun grupo coincide")
    empty_message = _("Los grupos agrupan oficinas entre las que la flota se mueve libre.")
    page_title = _("Grupos de oficinas")
    create_url_name = "offices:pool_create"
    create_label = _("Nuevo grupo")
    create_permission = "offices.add_officepool"

    def get_base_queryset(self):
        return super().get_base_queryset().prefetch_related("offices")


class OfficePoolFormMixin:
    model = OfficePool
    form_class = OfficePoolForm
    table_id = "tabla-grupos"
    list_url_name = "offices:pool_list"

    def save_object(self, form):
        return save_pool(pool=form.save(commit=False), actor=self.request.user)


class OfficePoolCreateView(OfficePoolFormMixin, ModalCreateView):
    permission_required = "offices.add_officepool"
    modal_title = _("Nuevo grupo de oficinas")
    submit_label = _("Crear grupo")
    success_message = _("Grupo %(objeto)s creado.")


class OfficePoolUpdateView(OfficePoolFormMixin, ModalUpdateView):
    permission_required = "offices.change_officepool"
    modal_title = _("Editar grupo de oficinas")
    success_message = _("Grupo %(objeto)s actualizado.")


class OfficePoolToggleView(ToggleActiveView):
    permission_required = "offices.change_officepool"
    model = OfficePool
    list_url_name = "offices:pool_list"
    activated_message = _("Grupo %(objeto)s reactivado.")
    deactivated_message = _("Grupo %(objeto)s desactivado.")

    def perform(self, objeto):
        set_pool_active(pool=objeto, active=self.activate, actor=self.request.user)


class OfficePoolActivateView(OfficePoolToggleView):
    activate = True


class OfficePoolDeactivateView(OfficePoolToggleView):
    activate = False
