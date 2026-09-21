"""Pantallas de flota. Mismo patron CRUD que oficinas (docs/patrones/crud.md)."""

from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View

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
from apps.core.services import ServiceError
from apps.core.tables import Column

from .filters import MaintenanceFilter, VehicleBlockFilter, VehicleCategoryFilter, VehicleFilter
from .forms import (
    FinishWorkshopForm,
    MaintenanceRecordForm,
    StartWorkshopForm,
    VehicleBlockForm,
    VehicleCategoryForm,
    VehicleForm,
    VehicleStatusForm,
)
from .models import MaintenanceRecord, Vehicle, VehicleBlock, VehicleCategory
from .selectors import blocks_for_user
from .services import (
    delete_block,
    save_block,
    save_category,
    save_vehicle,
    set_category_active,
    set_vehicle_active,
    set_vehicle_status,
)

# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------


class VehicleCategoryListView(CrudListView):
    permission_required = "fleet.view_vehiclecategory"
    model = VehicleCategory
    filterset_class = VehicleCategoryFilter
    table_id = "tabla-categorias"
    table_row_template = "fleet/_category_row.html"
    table_columns = [
        Column(label=_("Orden"), css="tabular w-20"),
        Column(label=_("Codigo"), css="font-mono tabular w-28"),
        Column(label=_("Categoria")),
        Column(label=_("Plazas/Puertas"), css="tabular w-32"),
        Column(label=_("Cambio")),
        Column(label=_("Combustible")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Codigo o nombre...")
    empty_title = _("Ninguna categoria coincide")
    empty_message = _("Cambia la busqueda o quita algun filtro.")
    page_title = _("Categorias de vehiculo")
    create_url_name = "fleet:category_create"
    create_label = _("Nueva categoria")
    create_permission = "fleet.add_vehiclecategory"


class VehicleCategoryFormMixin:
    model = VehicleCategory
    form_class = VehicleCategoryForm
    table_id = "tabla-categorias"
    list_url_name = "fleet:category_list"

    def save_object(self, form):
        return save_category(category=form.save(commit=False), actor=self.request.user)


class VehicleCategoryCreateView(VehicleCategoryFormMixin, ModalCreateView):
    permission_required = "fleet.add_vehiclecategory"
    modal_title = _("Nueva categoria")
    submit_label = _("Crear categoria")
    success_message = _("Categoria %(objeto)s creada.")


class VehicleCategoryUpdateView(VehicleCategoryFormMixin, ModalUpdateView):
    permission_required = "fleet.change_vehiclecategory"
    modal_title = _("Editar categoria")
    success_message = _("Categoria %(objeto)s actualizada.")


class VehicleCategoryToggleView(ToggleActiveView):
    permission_required = "fleet.change_vehiclecategory"
    model = VehicleCategory
    list_url_name = "fleet:category_list"
    activated_message = _("Categoria %(objeto)s de vuelta en el catalogo.")
    deactivated_message = _("Categoria %(objeto)s retirada del catalogo.")

    def perform(self, objeto):
        set_category_active(category=objeto, active=self.activate, actor=self.request.user)


class VehicleCategoryActivateView(VehicleCategoryToggleView):
    activate = True


class VehicleCategoryDeactivateView(VehicleCategoryToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Vehiculos
# ---------------------------------------------------------------------------


class VehicleListView(CrudListView):
    permission_required = "fleet.view_vehicle"
    model = Vehicle
    filterset_class = VehicleFilter
    scope_to_user = True  # la flota es dato operativo: solo la de tus oficinas
    table_id = "tabla-vehiculos"
    table_row_template = "fleet/_vehicle_row.html"
    table_columns = [
        Column(label=_("Matricula"), css="font-mono tabular w-32"),
        Column(label=_("Vehiculo")),
        Column(label=_("Categoria")),
        Column(label=_("Oficina")),
        Column(label=_("Km"), align="right", css="tabular w-24"),
        Column(label=_("Documentacion")),
        Column(label=_("Situacion")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Matricula, marca, modelo o bastidor...")
    empty_title = _("Ningun vehiculo coincide")
    empty_message = _("Cambia la busqueda o quita algun filtro.")
    page_title = _("Vehiculos")
    create_url_name = "fleet:vehicle_create"
    create_label = _("Nuevo vehiculo")
    create_permission = "fleet.add_vehicle"

    def get_base_queryset(self):
        return super().get_base_queryset().select_related("category", "current_office")


class VehicleFormMixin:
    model = Vehicle
    form_class = VehicleForm
    table_id = "tabla-vehiculos"
    list_url_name = "fleet:vehicle_list"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # El formulario recorta oficinas y categorias a lo que el usuario puede.
        kwargs["user"] = self.request.user
        return kwargs

    def save_object(self, form):
        return save_vehicle(vehicle=form.save(commit=False), actor=self.request.user)


class VehicleCreateView(VehicleFormMixin, ModalCreateView):
    permission_required = "fleet.add_vehicle"
    modal_title = _("Nuevo vehiculo")
    submit_label = _("Dar de alta")
    success_message = _("Vehiculo %(objeto)s dado de alta.")


class VehicleUpdateView(VehicleFormMixin, ModalUpdateView):
    permission_required = "fleet.change_vehicle"
    modal_title = _("Editar vehiculo")
    success_message = _("Vehiculo %(objeto)s actualizado.")

    def get_base_queryset(self):
        # Un coche de otra oficina no existe para este usuario: 404, no 403.
        return Vehicle.objects.for_user(self.request.user)


class VehicleStatusUpdateView(ModalFormView):
    """Cambio manual de situacion. El servicio decide si el cambio no miente."""

    permission_required = "fleet.change_vehicle"
    form_class = VehicleStatusForm
    table_id = "tabla-vehiculos"
    list_url_name = "fleet:vehicle_list"
    modal_title = _("Cambiar situacion")
    submit_label = _("Cambiar")
    success_message = _("%(objeto)s: situacion actualizada.")

    @property
    def vehicle(self):
        if not hasattr(self, "_vehicle"):
            self._vehicle = get_object_or_404(
                Vehicle.objects.for_user(self.request.user), pk=self.kwargs["pk"]
            )
        return self._vehicle

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["vehicle"] = self.vehicle
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["modal_title"] = _("Situacion de %(matricula)s") % {
            "matricula": self.vehicle.plate
        }
        return contexto

    def save_object(self, form):
        return set_vehicle_status(
            vehicle=self.vehicle,
            status=form.cleaned_data["status"],
            actor=self.request.user,
        )


class VehicleToggleView(ToggleActiveView):
    permission_required = "fleet.change_vehicle"
    model = Vehicle
    list_url_name = "fleet:vehicle_list"
    activated_message = _("Vehiculo %(objeto)s de vuelta en la flota.")
    deactivated_message = _("Vehiculo %(objeto)s dado de baja.")

    def get_queryset(self):
        return Vehicle.objects.for_user(self.request.user)

    def perform(self, objeto):
        set_vehicle_active(vehicle=objeto, active=self.activate, actor=self.request.user)


class VehicleActivateView(VehicleToggleView):
    activate = True


class VehicleDeactivateView(VehicleToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Bloqueos
# ---------------------------------------------------------------------------


class VehicleBlockListView(CrudListView):
    permission_required = "fleet.view_vehicleblock"
    model = VehicleBlock
    filterset_class = VehicleBlockFilter
    table_id = "tabla-bloqueos"
    table_row_template = "fleet/_block_row.html"
    table_columns = [
        Column(label=_("Matricula"), css="font-mono tabular w-32"),
        Column(label=_("Vehiculo")),
        Column(label=_("Desde"), css="tabular"),
        Column(label=_("Hasta"), css="tabular"),
        Column(label=_("Motivo")),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Matricula, vehiculo o notas...")
    empty_title = _("Ningun bloqueo coincide")
    empty_message = _("Un bloqueo aparta un coche del alquiler entre dos fechas.")
    page_title = _("Bloqueos de vehiculo")
    create_url_name = "fleet:block_create"
    create_label = _("Nuevo bloqueo")
    create_permission = "fleet.add_vehicleblock"

    def get_base_queryset(self):
        # El scope se aplica por el vehiculo: el bloqueo no tiene oficina propia.
        return blocks_for_user(self.request.user)


class VehicleBlockFormMixin:
    model = VehicleBlock
    form_class = VehicleBlockForm
    table_id = "tabla-bloqueos"
    list_url_name = "fleet:block_list"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def save_object(self, form):
        return save_block(block=form.save(commit=False), actor=self.request.user)


class VehicleBlockCreateView(VehicleBlockFormMixin, ModalCreateView):
    permission_required = "fleet.add_vehicleblock"
    modal_title = _("Nuevo bloqueo")
    submit_label = _("Bloquear")
    success_message = _("Bloqueo creado para %(objeto)s.")


class VehicleBlockUpdateView(VehicleBlockFormMixin, ModalUpdateView):
    permission_required = "fleet.change_vehicleblock"
    modal_title = _("Editar bloqueo")
    success_message = _("Bloqueo actualizado.")

    def get_base_queryset(self):
        return blocks_for_user(self.request.user)


class VehicleBlockDeleteView(CrudPermissionMixin, View):
    """Anula un bloqueo.

    Unico borrado fisico del sistema, y con motivo: un bloqueo es agenda, no
    historico, y mientras la fila exista sigue ocupando hueco en la constraint
    de exclusion (ver `services.delete_block`).
    """

    permission_required = "fleet.delete_vehicleblock"

    def post(self, request, pk, *args, **kwargs):
        bloqueo = get_object_or_404(blocks_for_user(request.user), pk=pk)
        matricula = bloqueo.vehicle.plate
        delete_block(block=bloqueo, actor=request.user)
        mensaje = _("Bloqueo de %(matricula)s anulado.") % {"matricula": matricula}

        if not request.htmx:
            messages.success(request, mensaje)
            return HttpResponseRedirect(reverse("fleet:block_list"))

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, mensaje, "success")


# ---------------------------------------------------------------------------
# Mantenimiento
# ---------------------------------------------------------------------------


class MaintenanceListView(CrudListView):
    """Lo que toca pronto, arriba; debajo, todo lo programado y lo hecho."""

    permission_required = "fleet.view_maintenancerecord"
    model = MaintenanceRecord
    template_name = "fleet/maintenance_list.html"
    filterset_class = MaintenanceFilter
    table_id = "tabla-mantenimiento"
    table_row_template = "fleet/_maintenance_row.html"
    table_columns = [
        Column(label=_("Vehículo")),
        Column(label=_("Tipo")),
        Column(label=_("Fecha")),
        Column(label=_("Taller")),
        Column(label=_("Próximo")),
        Column(label=_("Coste"), align="right"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Matrícula, taller o descripción...")
    empty_title = _("Sin mantenimientos")
    empty_message = _("Programa el primero: revisión, aceite, ITV, neumáticos...")
    page_title = _("Mantenimiento")
    create_url_name = "fleet:maintenance_create"
    create_label = _("Programar mantenimiento")
    create_permission = "fleet.add_maintenancerecord"

    def get_base_queryset(self):
        return MaintenanceRecord.objects.for_user(self.request.user).select_related("vehicle")

    def get_context_data(self, **kwargs):
        from .maintenance import upcoming

        contexto = super().get_context_data(**kwargs)
        contexto["vencimientos"] = upcoming(self.request.user)
        return contexto


class MaintenanceFormMixin:
    model = MaintenanceRecord
    form_class = MaintenanceRecordForm
    table_id = "tabla-mantenimiento"
    list_url_name = "fleet:maintenance_list"
    modal_width = "max-w-2xl"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def save_object(self, form):
        from .maintenance import save_record

        return save_record(record=form.save(commit=False), actor=self.request.user)


class MaintenanceCreateView(MaintenanceFormMixin, ModalCreateView):
    permission_required = "fleet.add_maintenancerecord"
    modal_title = _("Programar mantenimiento")
    submit_label = _("Programar")
    success_message = _("Mantenimiento %(objeto)s programado.")


class MaintenanceUpdateView(MaintenanceFormMixin, ModalUpdateView):
    permission_required = "fleet.change_maintenancerecord"
    modal_title = _("Editar mantenimiento")
    success_message = _("Mantenimiento %(objeto)s actualizado.")

    def get_base_queryset(self):
        return MaintenanceRecord.objects.for_user(self.request.user)


class MaintenanceStepView(ModalFormView):
    """Entrar en taller o salir de el: un formulario corto y el servicio decide."""

    permission_required = "fleet.change_maintenancerecord"
    table_id = "tabla-mantenimiento"
    list_url_name = "fleet:maintenance_list"

    @property
    def record(self):
        if not hasattr(self, "_registro"):
            self._registro = get_object_or_404(
                MaintenanceRecord.objects.for_user(self.request.user).select_related("vehicle"),
                pk=self.kwargs["pk"],
            )
        return self._registro


class MaintenanceStartView(MaintenanceStepView):
    form_class = StartWorkshopForm
    modal_title = _("Entrar en taller")
    submit_label = _("Meter en taller")

    def get_initial(self):
        from django.utils import timezone

        ahora = timezone.localtime().replace(second=0, microsecond=0)
        return {"started_at": ahora, "expected_end_at": ahora.replace(hour=19, minute=0)}

    def save_object(self, form):
        from .maintenance import start_workshop

        self.afectadas = start_workshop(
            record=self.record,
            started_at=form.cleaned_data["started_at"],
            expected_end_at=form.cleaned_data["expected_end_at"],
            actor=self.request.user,
        )
        return self.record

    def get_success_message(self, objeto) -> str:
        if self.afectadas:
            return _("%(coche)s en taller. Sin coche ahora: %(reservas)s.") % {
                "coche": objeto.vehicle.plate,
                "reservas": ", ".join(r.number for r in self.afectadas),
            }
        return _("%(coche)s en taller.") % {"coche": objeto.vehicle.plate}


class MaintenanceFinishView(MaintenanceStepView):
    form_class = FinishWorkshopForm
    modal_title = _("Salida del taller")
    submit_label = _("Dar por hecho")

    def get_initial(self):
        from django.utils import timezone

        return {
            "finished_at": timezone.localtime().replace(second=0, microsecond=0),
            "mileage": self.record.vehicle.mileage,
        }

    def save_object(self, form):
        from .maintenance import finish_workshop

        return finish_workshop(record=self.record, actor=self.request.user, **form.cleaned_data)

    def get_success_message(self, objeto) -> str:
        return _("%(que)s hecho. El coche vuelve a flota.") % {"que": objeto}


class MaintenanceCancelView(CrudPermissionMixin, View):
    permission_required = "fleet.change_maintenancerecord"

    def post(self, request, pk, *args, **kwargs):
        from .maintenance import cancel_record

        registro = get_object_or_404(MaintenanceRecord.objects.for_user(request.user), pk=pk)
        try:
            cancel_record(record=registro, actor=request.user)
        except ServiceError as exc:
            return trigger_toast(HttpResponse(status=200), str(exc), "warning")
        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_GUARDADO)
        return trigger_toast(respuesta, _("Mantenimiento anulado."), "success")
