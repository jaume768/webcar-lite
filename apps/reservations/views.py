"""Pantallas de reservas: listado, ficha y alta rapida de mostrador."""

import structlog
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, View

from apps.availability.services import (
    AvailabilityError,
    check_category_availability,
    get_available_vehicles,
)
from apps.core.crud import CrudListView, CrudPermissionMixin
from apps.core.htmx import trigger_toast
from apps.core.services import ServiceError
from apps.core.tables import Column
from apps.customers.models import Customer
from apps.pricing.dto import ExtraRequest
from apps.pricing.services import PricingError

from .filters import ReservationFilter
from .forms import QuickCustomerForm, QuickReservationForm, TransitionForm
from .models import Reservation
from .services import create_quick_reservation, quote
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


class ReservationDetailView(CrudPermissionMixin, DetailView):
    permission_required = "reservations.view_reservation"
    model = Reservation
    template_name = "reservations/reservation_detail.html"
    context_object_name = "reservation"

    def get_queryset(self):
        return Reservation.objects.for_user(self.request.user).select_related(
            "customer", "category", "vehicle", "rate", "pickup_office", "return_office"
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        reserva = self.object
        contexto["page_title"] = _("Reserva %(numero)s") % {"numero": reserva.number}
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Reservas"), "url": reverse("reservations:list")},
            {"label": reserva.number},
        ]
        contexto["transiciones"] = available_transitions(reserva, self.request.user)
        contexto["lineas"] = reserva.price_breakdown.get("lines", [])
        contexto["historico"] = reserva.status_changes.select_related("changed_by")
        contexto["extras"] = reserva.extras.all()
        return contexto


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


class AssignVehicleView(CrudPermissionMixin, View):
    """Asigna un coche concreto a una reserva ya creada."""

    permission_required = "reservations.change_reservation"

    def post(self, request, pk, *args, **kwargs):
        from apps.availability.services import assign_vehicle
        from apps.fleet.models import Vehicle

        reserva = get_object_or_404(Reservation.objects.for_user(request.user), pk=pk)
        vehiculo = get_object_or_404(
            Vehicle.objects.for_user(request.user), pk=request.POST.get("vehicle")
        )

        try:
            assign_vehicle(reservation=reserva, vehicle=vehiculo, actor=request.user)
        except ServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Vehiculo %(matricula)s asignado.") % {"matricula": vehiculo.plate},
            )
        return HttpResponseRedirect(reverse("reservations:detail", args=[reserva.pk]))
