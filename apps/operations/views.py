"""Pantallas de entrega y devolucion."""

import structlog
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from apps.core.crud import CrudPermissionMixin
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.services import ServiceError
from apps.reservations.models import Reservation

from .forms import CheckInForm, CheckOutForm, DamageForm
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
