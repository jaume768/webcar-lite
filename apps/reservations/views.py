"""Pantallas de reservas: listado, ficha y alta rapida de mostrador."""

import structlog
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from apps.availability.services import (
    AvailabilityError,
    VehicleNotAvailableError,
    check_category_availability,
    get_available_vehicles,
    vehicle_options,
)
from apps.core.crud import CrudListView, CrudPermissionMixin
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.services import ServiceError
from apps.core.tables import Column
from apps.customers.models import Customer
from apps.pricing.dto import ExtraRequest
from apps.pricing.services import PricingError

from .filters import ReservationFilter
from .forms import (
    AddExtraForm,
    ChangeCategoryForm,
    ChangeDatesForm,
    DriverForm,
    ManualPriceForm,
    QuickCustomerForm,
    QuickReservationForm,
    TransitionForm,
)
from .models import Reservation
from .selectors import header_data, timeline
from .services import (
    ManualPriceWouldBeLost,
    _avisar_precio_manual,
    add_driver,
    add_extra,
    apply_change,
    create_quick_reservation,
    preview_change,
    price_preview,
    quote,
    recalculate_price,
    release_vehicle,
    remove_driver,
    remove_extra,
    set_extra_quantity,
    set_manual_price,
)
from .state_machine import (
    InvalidTransition,
    TransitionRefused,
    allowed_targets,
    available_transitions,
    transition,
)

logger = structlog.get_logger(__name__)


class ReservationListView(CrudListView):
    permission_required = "reservations.view_reservation"
    model = Reservation
    filterset_class = ReservationFilter
    table_id = "tabla-reservas"
    table_row_template = "reservations/_reservation_row.html"
    table_columns = [
        Column(label=_("Numero"), css="font-mono tabular w-32"),
        Column(label=_("Cliente")),
        Column(label=_("Categoria")),
        Column(label=_("Recogida")),
        Column(label=_("Devolucion")),
        Column(label=_("Estado")),
        Column(label=_("Total"), align="right", css="tabular w-28"),
    ]
    search_placeholder = _("Numero, cliente o matricula...")
    empty_title = _("Ninguna reserva coincide")
    empty_message = _("Cambia la busqueda o quita algun filtro.")
    page_title = _("Reservas")
    scope_to_user = True

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .select_related("customer", "category", "vehicle", "pickup_office", "return_office")
        )


#: Pestanas de la ficha, en orden. `ready` a False las deja visibles pero
#: apagadas: asi se ve el mapa completo de la ficha sin fingir que funcionan.
TABS = [
    ("resumen", _("Resumen"), True),
    ("cliente", _("Cliente"), True),
    ("vehiculo", _("Vehiculo"), True),
    ("extras", _("Extras"), True),
    ("precio", _("Precio"), True),
    ("cobros", _("Cobros"), True),
    ("checkin", _("Check-in"), False),
    ("checkout", _("Check-out"), False),
    ("documentos", _("Documentos"), False),
    ("historial", _("Historial"), True),
]
TABS_LISTAS = {codigo for codigo, _etiqueta, lista in TABS if lista}
TAB_POR_DEFECTO = "resumen"


class ReservationBaseView(CrudPermissionMixin):
    """Todo lo que cuelga de una reserva concreta, ya con scope de oficina."""

    permission_required = "reservations.view_reservation"

    def get_reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related(
                    "customer", "category", "vehicle", "rate", "pickup_office", "return_office"
                ),
                pk=self.kwargs["pk"],
            )
        return self._reserva

    def contexto_de_ficha(self, **extra) -> dict:
        reserva = self.get_reservation()
        datos = {
            "reservation": reserva,
            "header": header_data(reserva),
            "transiciones": available_transitions(reserva, self.request.user),
        }
        datos.update(extra)
        return datos


class ReservationDetailView(ReservationBaseView, TemplateView):
    """Ficha completa: cabecera fija mas la pestana que toque."""

    template_name = "reservations/reservation_detail.html"

    def get_context_data(self, **kwargs):
        reserva = self.get_reservation()
        pestana = self.kwargs.get("tab") or TAB_POR_DEFECTO
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            self.contexto_de_ficha(
                page_title=_("Reserva %(numero)s") % {"numero": reserva.number},
                breadcrumbs=[
                    {"label": _("Inicio"), "url": reverse("core:home")},
                    {"label": _("Reservas"), "url": reverse("reservations:list")},
                    {"label": reserva.number},
                ],
                tabs=TABS,
                tab_activa=pestana,
                **contexto_de_pestana(self.request, reserva, pestana),
            )
        )
        return contexto


class ReservationTabView(ReservationBaseView, TemplateView):
    """Una pestana suelta, para que HTMX cambie solo el panel."""

    def get_template_names(self):
        pestana = self.kwargs["tab"]
        if pestana not in TABS_LISTAS:
            return ["reservations/tabs/_pendiente.html"]
        return [f"reservations/tabs/_{pestana}.html"]

    def get_context_data(self, **kwargs):
        reserva = self.get_reservation()
        pestana = self.kwargs["tab"]
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            self.contexto_de_ficha(
                tabs=TABS,
                tab_activa=pestana,
                **contexto_de_pestana(self.request, reserva, pestana),
            )
        )
        return contexto


class ReservationHeaderView(ReservationBaseView, TemplateView):
    """Solo la cabecera. La pide HTMX despues de cualquier cambio."""

    template_name = "reservations/_header.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        return contexto


def contexto_de_pestana(request, reserva: Reservation, pestana: str) -> dict:
    """Lo que necesita cada pestana. Fuera de aqui nadie consulta de mas."""
    if pestana == "resumen":
        return {
            "lineas": reserva.price_breakdown.get("lines", []),
            "extras": reserva.extras.all(),
            "ultimos_cambios": timeline(reserva)[:5],
        }
    if pestana == "cliente":
        return {"conductores": reserva.drivers.all()}
    if pestana == "vehiculo":
        return {
            "opciones": vehicle_options(
                reserva.category,
                reserva.pickup_office,
                reserva.pickup_at,
                reserva.return_at,
                exclude_reservation=reserva,
                rotation_minutes=reserva.rotation_minutes,
            )
        }
    if pestana == "extras":
        return {
            "extras": reserva.extras.select_related("extra"),
            "form_extra": AddExtraForm(reservation=reserva),
        }
    if pestana == "precio":
        return {
            "lineas": reserva.price_breakdown.get("lines", []),
            "desglose": reserva.price_breakdown,
            "cambios_de_precio": reserva.price_changes.select_related("changed_by"),
        }
    if pestana == "cobros":
        from apps.billing.selectors import summary

        return {
            "cobros": reserva.payments.select_related("created_by", "office"),
            "saldo": summary(reserva),
        }
    if pestana == "historial":
        return {"historial": timeline(reserva)}
    return {}


class ReservationTransitionView(CrudPermissionMixin, FormView):
    """Un cambio de estado, siempre por la maquina de estados."""

    permission_required = "reservations.view_reservation"
    template_name = "reservations/_transition_modal.html"
    form_class = TransitionForm

    @property
    def reservation(self):
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user), pk=self.kwargs["pk"]
            )
        return self._reserva

    @property
    def salto(self):
        destino = self.kwargs["to_status"]
        salto = allowed_targets(self.reservation.status).get(destino)
        if salto is None:
            raise InvalidTransition(
                _("La reserva ya no admite esa operacion. Vuelve a cargar la ficha.")
            )
        return salto

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["requires_reason"] = self.salto.requires_reason
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["reservation"] = self.reservation
        contexto["salto"] = self.salto
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        try:
            transition(
                self.reservation,
                self.salto.to,
                self.request.user,
                reason=form.cleaned_data.get("reason", ""),
            )
        except (InvalidTransition, TransitionRefused, ServiceError) as exc:
            # Regla de negocio, no fallo: se cuenta y se vuelve al formulario.
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Reserva %(numero)s: %(accion)s.")
            % {"numero": self.reservation.number, "accion": self.salto.label},
        )
        respuesta = HttpResponse(status=200)
        respuesta.headers["HX-Redirect"] = reverse(
            "reservations:detail", args=[self.reservation.pk]
        )
        return respuesta

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


# ---------------------------------------------------------------------------
# Alta rapida
# ---------------------------------------------------------------------------


class QuickReservationView(CrudPermissionMixin, FormView):
    permission_required = "reservations.add_reservation"
    template_name = "reservations/quick_reservation.html"
    form_class = QuickReservationForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        from apps.offices.selectors import get_active_office

        inicial = super().get_initial()
        activa = get_active_office(self.request)
        if activa is not None:
            inicial["pickup_office"] = activa
        inicial["days"] = 3
        return inicial

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["page_title"] = _("Reserva rapida")
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Reservas"), "url": reverse("reservations:list")},
            {"label": _("Reserva rapida")},
        ]
        return contexto

    def form_valid(self, form):
        datos = form.cleaned_data
        try:
            reserva = create_quick_reservation(
                category=datos["category"],
                pickup_office=datos["pickup_office"],
                return_office=datos["return_office"],
                pickup_at=datos["pickup_at"],
                return_at=datos["return_at"],
                customer=datos["customer"],
                extras=[ExtraRequest(extra=extra, quantity=1) for extra in datos["extras"]],
                fuel_policy=datos["fuel_policy"],
                cancellation_policy=datos["cancellation_policy"],
                notes=datos["notes"],
                actor=self.request.user,
            )
        except (AvailabilityError, PricingError, ServiceError) as exc:
            # No hay hueco o no hay tarifa: se avisa y no se ha escrito nada.
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Reserva %(numero)s creada por %(total)s EUR.")
            % {"numero": reserva.number, "total": reserva.total},
        )
        return HttpResponseRedirect(reverse("reservations:detail", args=[reserva.pk]))


class QuickReservationPreview(CrudPermissionMixin, View):
    """Disponibilidad y precio en vivo, mientras el mostrador teclea.

    No escribe nada: es la respuesta a "y una semana cuanto sale?".
    """

    permission_required = "reservations.add_reservation"

    def get(self, request, *args, **kwargs):
        form = QuickReservationForm(request.GET or None, user=request.user)
        contexto = {"form": form}

        # Con el formulario a medias no se calcula nada: es lo normal mientras
        # se teclea, no un error que ensenar.
        if not form.is_valid():
            contexto["incompleto"] = True
            return render(request, "reservations/_preview.html", contexto)

        datos = form.cleaned_data
        contexto["disponibilidad"] = check_category_availability(
            datos["category"], datos["pickup_office"], datos["pickup_at"], datos["return_at"]
        )
        contexto["vehiculos_libres"] = get_available_vehicles(
            datos["category"], datos["pickup_office"], datos["pickup_at"], datos["return_at"]
        ).count()

        try:
            contexto["precio"] = quote(
                category=datos["category"],
                pickup_office=datos["pickup_office"],
                return_office=datos["return_office"],
                pickup_at=datos["pickup_at"],
                return_at=datos["return_at"],
                extras=[ExtraRequest(extra=extra, quantity=1) for extra in datos["extras"]],
                customer=datos["customer"],
            )
        except PricingError as exc:
            contexto["error_precio"] = str(exc)

        return render(request, "reservations/_preview.html", contexto)


class CustomerSearchView(CrudPermissionMixin, View):
    """Autocompletado de cliente por documento, telefono o nombre."""

    permission_required = "reservations.add_reservation"

    def get(self, request, *args, **kwargs):
        termino = request.GET.get("q", "").strip()
        clientes = Customer.objects.active().search(termino)[:10] if termino else []
        return render(
            request,
            "ui/_select_search_options.html",
            {
                "options": [
                    {
                        "value": cliente.pk,
                        "label": f"{cliente.first_name} {cliente.last_name}".strip(),
                        "hint": f"{cliente.document_number} · {cliente.phone}".strip(" ·"),
                    }
                    for cliente in clientes
                ]
            },
        )


class QuickCustomerCreateView(CrudPermissionMixin, FormView):
    """Alta de cliente al vuelo, en modal, sin salir de la reserva."""

    permission_required = "customers.add_customer"
    template_name = "reservations/_quick_customer_modal.html"
    form_class = QuickCustomerForm

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        from apps.customers.services import save_customer

        cliente = save_customer(customer=form.save(commit=False), actor=self.request.user)
        logger.info("cliente_alta_rapida", customer_id=cliente.pk, actor_id=self.request.user.pk)

        # El modal se cierra y el buscador de la reserva se queda con el cliente
        # ya elegido: quien esta en el mostrador no tiene que buscarlo otra vez.
        respuesta = render(
            self.request,
            "reservations/_customer_chosen.html",
            {"cliente": cliente},
        )
        return trigger_toast(
            respuesta,
            _("Cliente %(nombre)s dado de alta.") % {"nombre": cliente.first_name},
            "success",
        )

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


# ---------------------------------------------------------------------------
# Cambios desde la ficha
# ---------------------------------------------------------------------------

#: Lo escucha la cabecera y el panel de pestanas para repintarse sin recargar.
EVENTO_RESERVA = "reserva:actualizada"


def _respuesta_de_cambio(mensaje: str, nivel: str = "success") -> HttpResponse:
    """Cierra el modal y avisa a la ficha de que hay algo nuevo que leer."""
    respuesta = HttpResponse(status=200)
    trigger_event(respuesta, EVENTO_RESERVA)
    return trigger_toast(respuesta, mensaje, nivel)


class ReservationChangeView(ReservationBaseView, FormView):
    """Base de los cambios que revalidan disponibilidad y precio.

    Ensena siempre la diferencia antes de confirmar: en mostrador nadie acepta
    un cambio de fechas sin saber si sube o baja.
    """

    permission_required = "reservations.change_reservation"
    template_name = "reservations/_change_modal.html"
    titulo = ""

    def datos_de_cambio(self, form) -> dict:
        raise NotImplementedError

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.method == "GET" and self.request.GET:
            kwargs["data"] = self.request.GET
        return kwargs

    def get_initial(self):
        reserva = self.get_reservation()
        return {
            "pickup_at": reserva.pickup_at,
            "return_at": reserva.return_at,
            "category": reserva.category,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["titulo"] = self.titulo
        contexto["form_action"] = self.request.path

        form = contexto["form"]
        if form.is_bound and form.is_valid():
            contexto["preview"] = preview_change(
                reservation=self.get_reservation(),
                actor=self.request.user,
                **self.datos_de_cambio(form),
            )
        return contexto

    def get(self, request, *args, **kwargs):
        contexto = self.get_context_data()
        # Con `preview` solo viaja el panel de la diferencia, no el modal entero:
        # asi el formulario no se repinta mientras se teclea.
        if request.GET.get("preview"):
            return render(request, "reservations/_change_preview.html", contexto)
        return self.render_to_response(contexto)

    def form_valid(self, form):
        try:
            apply_change(
                reservation=self.get_reservation(),
                release_vehicle=bool(self.request.POST.get("release_vehicle")),
                actor=self.request.user,
                **self.datos_de_cambio(form),
            )
        except VehicleNotAvailableError as exc:
            # El coche asignado estorba: se nombra el conflicto y se ofrece
            # soltarlo, que es lo que resuelve la situacion en mostrador.
            contexto = self.get_context_data(form=form)
            contexto["conflicto_de_vehiculo"] = str(exc)
            return self.render_to_response(contexto, status=422)
        except (AvailabilityError, PricingError, ServiceError) as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _respuesta_de_cambio(_("Reserva actualizada."))

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class ChangeDatesView(ReservationChangeView):
    form_class = ChangeDatesForm
    titulo = _("Cambiar fechas")

    def datos_de_cambio(self, form) -> dict:
        return {
            "pickup_at": form.cleaned_data["pickup_at"],
            "return_at": form.cleaned_data["return_at"],
        }


class ChangeCategoryView(ReservationChangeView):
    form_class = ChangeCategoryForm
    titulo = _("Cambiar categoria")

    def datos_de_cambio(self, form) -> dict:
        return {"category": form.cleaned_data["category"]}


class AssignVehicleView(ReservationBaseView, TemplateView):
    """Elegir coche, con el motivo de cada descarte a la vista."""

    permission_required = "reservations.change_reservation"
    template_name = "reservations/_assign_vehicle_modal.html"

    def get_context_data(self, **kwargs):
        reserva = self.get_reservation()
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["form_action"] = self.request.path
        contexto["opciones"] = vehicle_options(
            reserva.category,
            reserva.pickup_office,
            reserva.pickup_at,
            reserva.return_at,
            exclude_reservation=reserva,
            rotation_minutes=reserva.rotation_minutes,
        )
        return contexto

    def post(self, request, *args, **kwargs):
        from apps.availability.services import assign_vehicle
        from apps.fleet.models import Vehicle

        reserva = self.get_reservation()
        vehiculo = get_object_or_404(
            Vehicle.objects.for_user(request.user), pk=request.POST.get("vehicle")
        )
        try:
            assign_vehicle(reservation=reserva, vehicle=vehiculo, actor=request.user)
        except ServiceError as exc:
            contexto = self.get_context_data()
            contexto["error"] = str(exc)
            return self.render_to_response(contexto, status=422)

        return _respuesta_de_cambio(
            _("Vehiculo %(matricula)s asignado.") % {"matricula": vehiculo.plate}
        )


class ReleaseVehicleView(ReservationBaseView, View):
    """Suelta el coche asignado sin tocar nada mas."""

    permission_required = "reservations.change_reservation"

    def post(self, request, *args, **kwargs):
        reserva = self.get_reservation()
        if not reserva.vehicle_id:
            return _respuesta_de_cambio(_("La reserva ya no tenia coche."), "info")

        matricula = reserva.vehicle.plate
        release_vehicle(reservation=reserva, actor=request.user)
        return _respuesta_de_cambio(
            _("%(matricula)s liberado. La reserva sigue viva contra su categoria.")
            % {"matricula": matricula}
        )


class DriverCreateView(ReservationBaseView, FormView):
    """Alta de conductor adicional, con el carnet validado."""

    permission_required = "reservations.change_reservation"
    template_name = "reservations/_driver_modal.html"
    form_class = DriverForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["reservation"] = self.get_reservation()
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        try:
            add_driver(
                reservation=self.get_reservation(),
                driver=form.save(commit=False),
                actor=self.request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _respuesta_de_cambio(_("Conductor autorizado."))

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class DriverDeleteView(ReservationBaseView, View):
    permission_required = "reservations.change_reservation"

    def post(self, request, *args, **kwargs):
        reserva = self.get_reservation()
        conductor = get_object_or_404(reserva.drivers, pk=kwargs["driver_pk"])
        remove_driver(driver=conductor, actor=request.user)
        return _respuesta_de_cambio(_("Conductor retirado."))


# ---------------------------------------------------------------------------
# Extras y precio
# ---------------------------------------------------------------------------


def _respuesta_de_precio_manual(vista, contexto, aviso: str) -> HttpResponse:
    """Devuelve el modal con el aviso y el boton que confirma pisar el precio."""
    contexto["aviso_precio_manual"] = aviso
    return vista.render_to_response(contexto, status=422)


class AddExtraView(ReservationBaseView, FormView):
    permission_required = "reservations.change_reservation"
    template_name = "reservations/_extra_modal.html"
    form_class = AddExtraForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["reservation"] = self.get_reservation()
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["form_action"] = self.request.path
        form = contexto["form"]
        if form.is_bound and form.is_valid():
            breakdown, diferencia = price_preview(
                reservation=self.get_reservation(),
                extras=(
                    *_extras_actuales(self.get_reservation()),
                    ExtraRequest(
                        extra=form.cleaned_data["extra"], quantity=form.cleaned_data["quantity"]
                    ),
                ),
            )
            contexto["preview_precio"] = breakdown
            contexto["diferencia"] = diferencia
        return contexto

    def get(self, request, *args, **kwargs):
        # Con `preview` viaja solo el panel del importe, no el modal entero.
        if request.GET.get("preview"):
            form = self.get_form_class()(request.GET, reservation=self.get_reservation())
            contexto = self.get_context_data(form=form)
            return render(request, "reservations/_price_diff.html", contexto)
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            add_extra(
                reservation=self.get_reservation(),
                extra=form.cleaned_data["extra"],
                quantity=form.cleaned_data["quantity"],
                confirm_manual_override=bool(self.request.POST.get("confirm_manual")),
                actor=self.request.user,
            )
        except ManualPriceWouldBeLost as exc:
            return _respuesta_de_precio_manual(self, self.get_context_data(form=form), str(exc))
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _respuesta_de_cambio(_("Extra anadido."))

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


def _extras_actuales(reserva):
    return tuple(
        ExtraRequest(extra=linea.extra, quantity=linea.quantity)
        for linea in reserva.extras.select_related("extra")
    )


class ExtraLineView(ReservationBaseView, View):
    """Cambiar cantidad o quitar una linea de extra."""

    permission_required = "reservations.change_reservation"

    def post(self, request, *args, **kwargs):
        reserva = self.get_reservation()
        linea = get_object_or_404(reserva.extras.select_related("extra"), pk=kwargs["line_pk"])
        confirmado = bool(request.POST.get("confirm_manual"))

        try:
            if request.POST.get("accion") == "quitar":
                remove_extra(
                    reservation=reserva,
                    line=linea,
                    confirm_manual_override=confirmado,
                    actor=request.user,
                )
                mensaje = _("Extra quitado.")
            else:
                set_extra_quantity(
                    reservation=reserva,
                    line=linea,
                    quantity=int(request.POST.get("quantity") or 1),
                    confirm_manual_override=confirmado,
                    actor=request.user,
                )
                mensaje = _("Cantidad actualizada.")
        except ManualPriceWouldBeLost as exc:
            # 409: no se ha hecho nada y hace falta una decision del usuario.
            respuesta = render(
                request,
                "reservations/_manual_price_confirm.html",
                {
                    "reservation": reserva,
                    "aviso": str(exc),
                    "reintento": request.POST.dict(),
                    "form_action": request.path,
                },
                status=409,
            )
            return respuesta
        except ServiceError as exc:
            return trigger_toast(HttpResponse(status=200), str(exc), "warning")

        return _respuesta_de_cambio(mensaje)


class ManualPriceView(ReservationBaseView, FormView):
    """Precio del alquiler puesto a mano. Permiso y motivo obligatorios."""

    permission_required = "reservations.change_reservation_price"
    template_name = "reservations/_manual_price_modal.html"
    form_class = ManualPriceForm

    def get_initial(self):
        reserva = self.get_reservation()
        return {"daily_price": reserva.price_breakdown.get("daily_price")}

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["form_action"] = self.request.path
        form = contexto["form"]
        if form.is_bound and form.is_valid():
            breakdown, diferencia = price_preview(
                reservation=self.get_reservation(),
                manual_override=form.cleaned_data["daily_price"],
            )
            contexto["preview_precio"] = breakdown
            contexto["diferencia"] = diferencia
        return contexto

    def get(self, request, *args, **kwargs):
        if request.GET.get("preview"):
            form = self.get_form_class()(request.GET)
            return render(
                request, "reservations/_price_diff.html", self.get_context_data(form=form)
            )
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            set_manual_price(
                reservation=self.get_reservation(),
                daily_price=form.cleaned_data["daily_price"],
                reason=form.cleaned_data["reason"],
                actor=self.request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _respuesta_de_cambio(_("Precio actualizado a mano."))

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class RecalculatePriceView(ReservationBaseView, TemplateView):
    """Rehace el precio desde la tarifa, ensenando antes la diferencia."""

    permission_required = "reservations.change_reservation"
    template_name = "reservations/_recalculate_modal.html"

    def get_context_data(self, **kwargs):
        reserva = self.get_reservation()
        contexto = super().get_context_data(**kwargs)
        contexto.update(self.contexto_de_ficha())
        contexto["form_action"] = self.request.path
        breakdown, diferencia = price_preview(reservation=reserva)
        contexto["preview_precio"] = breakdown
        contexto["diferencia"] = diferencia
        if reserva.is_price_manual:
            contexto["aviso_precio_manual"] = _avisar_precio_manual(reserva)
        return contexto

    def post(self, request, *args, **kwargs):
        try:
            recalculate_price(
                reservation=self.get_reservation(),
                confirm_manual_override=bool(request.POST.get("confirm_manual")),
                actor=request.user,
            )
        except ManualPriceWouldBeLost as exc:
            return _respuesta_de_precio_manual(self, self.get_context_data(), str(exc))
        except ServiceError as exc:
            contexto = self.get_context_data()
            contexto["error"] = str(exc)
            return self.render_to_response(contexto, status=422)

        return _respuesta_de_cambio(_("Precio recalculado desde la tarifa."))
