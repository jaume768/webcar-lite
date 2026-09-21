"""Pantallas de entrega y devolucion."""

import structlog
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from apps.core.crud import CrudListView, CrudPermissionMixin, ModalCreateView, ModalUpdateView
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.services import ServiceError
from apps.core.tables import Column
from apps.reservations.models import Reservation

from .filters import TrafficFineFilter
from .fines import close_fine, mark_identified, register_fine, rematch
from .forms import CheckInForm, CheckOutForm, DamageForm, TrafficFineForm
from .models import TrafficFine
from .services import perform_check_in, perform_check_out, preexisting_damages

logger = structlog.get_logger(__name__)

EVENTO_RESERVA = "reserva:actualizada"


def _hecho(mensaje) -> HttpResponse:
    respuesta = HttpResponse(status=200)
    trigger_event(respuesta, EVENTO_RESERVA)
    return trigger_toast(respuesta, mensaje, "success")


class OperacionBaseView(CrudPermissionMixin, FormView):
    permission_required = "reservations.change_reservation"

    def get_reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related(
                    "vehicle", "customer", "category", "return_office"
                ),
                pk=self.kwargs["pk"],
            )
        return self._reserva

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["reservation"] = self.get_reservation()
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["reservation"] = self.get_reservation()
        contexto["form_action"] = self.request.path
        return contexto

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class CheckInView(OperacionBaseView):
    template_name = "operations/_check_in_modal.html"
    form_class = CheckInForm

    def get_initial(self):
        reserva = self.get_reservation()
        return {
            "actual_datetime": timezone.now(),
            "mileage": reserva.vehicle.mileage if reserva.vehicle else 0,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["danos_previos"] = preexisting_damages(self.get_reservation())
        return contexto

    def form_valid(self, form):
        try:
            perform_check_in(
                reservation=self.get_reservation(),
                mileage=form.cleaned_data["mileage"],
                fuel_level=form.cleaned_data["fuel_level"],
                licence_verified=form.cleaned_data["licence_verified"],
                id_verified=form.cleaned_data["id_verified"],
                observations=form.cleaned_data["observations"],
                actual_datetime=form.cleaned_data["actual_datetime"],
                employee=self.request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _hecho(_("Coche entregado. La reserva esta en curso."))


class CheckOutView(OperacionBaseView):
    template_name = "operations/_check_out_modal.html"
    form_class = CheckOutForm

    def get_initial(self):
        reserva = self.get_reservation()
        entrega = getattr(reserva, "check_in", None)
        return {
            "actual_datetime": timezone.now(),
            "mileage": entrega.mileage if entrega else 0,
            "return_office": reserva.return_office,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        reserva = self.get_reservation()
        contexto["entrega"] = getattr(reserva, "check_in", None)
        contexto["danos_previos"] = preexisting_damages(reserva)
        return contexto

    def form_valid(self, form):
        try:
            perform_check_out(
                reservation=self.get_reservation(),
                mileage=form.cleaned_data["mileage"],
                fuel_level=form.cleaned_data["fuel_level"],
                return_office=form.cleaned_data["return_office"],
                observations=form.cleaned_data["observations"],
                actual_datetime=form.cleaned_data["actual_datetime"],
                manual_charges=form.manual_charges(),
                employee=self.request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        return _hecho(_("Coche devuelto. Revisa los cargos en la pestana de precio."))


class DamageCreateView(CrudPermissionMixin, FormView):
    """Parte de danos con croquis.

    Antes de la entrega el dano es preexistente; despues, nuevo. Lo decide el
    sistema, no un checkbox: es justo el dato que nadie debe poder falsear.
    """

    permission_required = "reservations.change_reservation"
    template_name = "operations/_damage_modal.html"
    form_class = DamageForm

    def get_reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related("vehicle"),
                pk=self.kwargs["pk"],
            )
        return self._reserva

    @property
    def es_preexistente(self) -> bool:
        return not hasattr(self.get_reservation(), "check_in")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["preexisting"] = self.es_preexistente
        return kwargs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["reservation"] = self.get_reservation()
        contexto["preexistente"] = self.es_preexistente
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        reserva = self.get_reservation()
        if reserva.vehicle_id is None:
            form.add_error(None, _("Sin vehiculo asignado no se puede anotar un dano."))
            return self.form_invalid(form)

        dano = form.save(commit=False)
        dano.reservation = reserva
        dano.vehicle = reserva.vehicle
        dano.is_preexisting = self.es_preexistente
        if dano.is_preexisting:
            dano.charge_to_customer = False
        dano.recorded_by = self.request.user
        dano.save()

        logger.info(
            "dano_anotado",
            reservation_number=reserva.number,
            zona=dano.zone,
            preexistente=dano.is_preexisting,
            employee_id=self.request.user.pk,
        )
        return _hecho(_("Dano anotado."))

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


# ---------------------------------------------------------------------------
# Multas
# ---------------------------------------------------------------------------


class TrafficFineListView(CrudListView):
    permission_required = "operations.view_trafficfine"
    model = TrafficFine
    scope_to_user = True
    filterset_class = TrafficFineFilter
    table_id = "tabla-multas"
    table_row_template = "operations/_fine_row.html"
    table_columns = [
        Column(label=_("Expediente")),
        Column(label=_("Infracción")),
        Column(label=_("Vehículo")),
        Column(label=_("Reserva / conductor")),
        Column(label=_("Importe"), align="right"),
        Column(label=_("Plazo")),
        Column(label=_("Estado")),
    ]
    search_placeholder = _("Expediente, matrícula, reserva o conductor...")
    empty_title = _("Sin multas")
    empty_message = _("Cuando llegue una, regístrala: se busca sola la reserva.")
    page_title = _("Multas")
    create_url_name = "operations:fine_create"
    create_label = _("Registrar multa")
    create_permission = "operations.add_trafficfine"

    def get_base_queryset(self):
        return super().get_base_queryset().select_related("vehicle", "reservation", "customer")


class TrafficFineFormMixin:
    model = TrafficFine
    form_class = TrafficFineForm
    table_id = "tabla-multas"
    list_url_name = "operations:fine_list"
    modal_width = "max-w-2xl"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def save_object(self, form):
        return register_fine(fine=form.save(commit=False), actor=self.request.user)


class TrafficFineCreateView(TrafficFineFormMixin, ModalCreateView):
    permission_required = "operations.add_trafficfine"
    modal_title = _("Registrar multa")
    submit_label = _("Registrar y buscar reserva")

    def get_success_message(self, objeto) -> str:
        if objeto.reservation_id:
            return _("Multa registrada: era de la reserva %(numero)s (%(cliente)s).") % {
                "numero": objeto.reservation.number,
                "cliente": objeto.driver_name,
            }
        return _("Multa registrada, pero ninguna reserva tenía el coche en ese momento.")


class TrafficFineUpdateView(TrafficFineFormMixin, ModalUpdateView):
    permission_required = "operations.change_trafficfine"
    modal_title = _("Editar multa")
    success_message = _("Multa %(objeto)s actualizada.")

    def get_base_queryset(self):
        return TrafficFine.objects.for_user(self.request.user)


class TrafficFineDetailView(CrudPermissionMixin, TemplateView):
    permission_required = "operations.view_trafficfine"
    template_name = "operations/fine_detail.html"

    def get_context_data(self, **kwargs):
        from urllib.parse import urlencode

        from django.urls import reverse

        from .fines import admin_fee

        multa = get_object_or_404(
            TrafficFine.objects.for_user(self.request.user).select_related(
                "vehicle", "reservation", "customer", "invoice", "office"
            ),
            pk=self.kwargs["pk"],
        )
        facturar = ""
        if multa.customer_id and not multa.invoice_id:
            facturar = (
                reverse("billing:manual_invoice")
                + "?"
                + urlencode(
                    {
                        "customer": multa.customer_id,
                        "office": multa.office_id,
                        "concept": _("Gestión de la multa %(expediente)s (%(organismo)s)")
                        % {"expediente": multa.file_number, "organismo": multa.authority},
                        "price": admin_fee(),
                        "notes": _("Infracción del %(fecha)s con el vehículo %(matricula)s.")
                        % {
                            "fecha": timezone.localtime(multa.offense_at).strftime(
                                "%d/%m/%Y %H:%M"
                            ),
                            "matricula": multa.vehicle.plate,
                        },
                        "multa": multa.pk,
                    }
                )
            )
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            {
                "fine": multa,
                "facturar_url": facturar,
                "adicionales": multa.reservation.drivers.all() if multa.reservation_id else [],
                "page_title": _("Multa %(expediente)s") % {"expediente": multa.file_number},
                "breadcrumbs": [
                    {"label": _("Inicio"), "url": reverse("core:home")},
                    {"label": _("Multas"), "url": reverse("operations:fine_list")},
                    {"label": multa.file_number},
                ],
            }
        )
        return contexto


class TrafficFineActionView(CrudPermissionMixin, View):
    """Reasignar, marcar identificado o cerrar. Siempre por POST."""

    permission_required = "operations.change_trafficfine"
    ACCIONES = {
        "reasignar": (rematch, _("Reserva buscada de nuevo.")),
        "identificado": (mark_identified, _("Conductor marcado como identificado.")),
        "cerrar": (close_fine, _("Multa cerrada.")),
    }

    def post(self, request, pk, accion, *args, **kwargs):
        from django.contrib import messages
        from django.http import Http404, HttpResponseRedirect
        from django.urls import reverse

        multa = get_object_or_404(TrafficFine.objects.for_user(request.user), pk=pk)
        if accion not in self.ACCIONES:
            raise Http404
        servicio, mensaje = self.ACCIONES[accion]
        try:
            servicio(fine=multa, actor=request.user)
        except ServiceError as exc:
            messages.warning(request, str(exc))
        else:
            messages.success(request, mensaje)
        return HttpResponseRedirect(reverse("operations:fine_detail", args=[multa.pk]))
