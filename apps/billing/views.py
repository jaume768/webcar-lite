"""Pantallas de cobros y arqueo de caja."""

import structlog
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.core.crud import CrudPermissionMixin
from apps.core.htmx import trigger_event, trigger_toast
from apps.core.services import ServiceError
from apps.reservations.models import Reservation

from .forms import CashRegisterForm, PaymentForm
from .selectors import cash_register, cash_totals_by_method, pending_amount
from .services import OverpaymentNotAllowed, refund, register_payment

logger = structlog.get_logger(__name__)

EVENTO_RESERVA = "reserva:actualizada"


class PaymentCreateView(CrudPermissionMixin, FormView):
    """Registrar un cobro desde la ficha de la reserva."""

    permission_required = "billing.add_payment"
    template_name = "billing/_payment_modal.html"
    form_class = PaymentForm

    @property
    def reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user), pk=self.kwargs["pk"]
            )
        return self._reserva

    def get_initial(self):
        return {"amount": pending_amount(self.reservation) or None}

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["reservation"] = self.reservation
        contexto["pendiente"] = pending_amount(self.reservation)
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        datos = form.cleaned_data
        try:
            register_payment(
                reservation=self.reservation,
                amount=datos["amount"],
                method=datos["method"],
                payment_type=datos["payment_type"],
                reference=datos["reference"],
                notes=datos["notes"],
                office=self.reservation.pickup_office,
                allow_overpayment=bool(self.request.POST.get("allow_overpayment")),
                actor=self.request.user,
            )
        except OverpaymentNotAllowed as exc:
            # No se ha escrito nada: hace falta que alguien lo autorice.
            contexto = self.get_context_data(form=form)
            contexto["aviso_sobrepago"] = str(exc)
            contexto["puede_autorizar"] = self.request.user.has_perm("billing.allow_overpayment")
            return self.render_to_response(contexto, status=422)
        except PermissionDenied:
            raise
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_RESERVA)
        return trigger_toast(respuesta, _("Cobro registrado."), "success")

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class PaymentRefundView(CrudPermissionMixin, FormView):
    """Apunte contrario de un cobro. El original no se toca."""

    permission_required = "billing.add_payment"
    template_name = "billing/_refund_modal.html"
    form_class = PaymentForm

    @property
    def payment(self):
        from .models import Payment

        if not hasattr(self, "_pago"):
            self._pago = get_object_or_404(
                Payment.objects.for_user(self.request.user), pk=self.kwargs["payment_pk"]
            )
        return self._pago

    def get_initial(self):
        return {
            "amount": self.payment.amount,
            "method": self.payment.method,
            "payment_type": self.payment.payment_type,
        }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["payment"] = self.payment
        contexto["reservation"] = self.payment.reservation
        contexto["form_action"] = self.request.path
        return contexto

    def form_valid(self, form):
        try:
            refund(
                payment=self.payment,
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["notes"],
                actor=self.request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_RESERVA)
        return trigger_toast(respuesta, _("Devolucion registrada."), "success")

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form), status=422)


class CashRegisterView(CrudPermissionMixin, TemplateView):
    """Arqueo: el efectivo de una oficina en un dia."""

    permission_required = "billing.view_billing"
    template_name = "billing/cash_register.html"

    def get_context_data(self, **kwargs):
        from apps.offices.selectors import get_active_office

        contexto = super().get_context_data(**kwargs)
        form = CashRegisterForm(self.request.GET or None, user=self.request.user)

        activa = get_active_office(self.request)
        dia = timezone.localdate()
        oficina = activa
        if form.is_valid():
            dia = form.cleaned_data["day"]
            oficina = form.cleaned_data["office"]
        else:
            form = CashRegisterForm(initial={"day": dia, "office": oficina}, user=self.request.user)

        contexto["form"] = form
        contexto["page_title"] = _("Arqueo de caja")
        contexto["breadcrumbs"] = [
            {"label": _("Inicio"), "url": reverse("core:home")},
            {"label": _("Arqueo de caja")},
        ]
        if oficina is not None:
            contexto["arqueo"] = cash_register(office=oficina, day=dia)
            contexto["por_metodo"] = cash_totals_by_method(office=oficina, day=dia)
        return contexto

    def render_to_response(self, context, **kwargs):
        if self.request.htmx:
            return render(self.request, "billing/_cash_register_panel.html", context)
        return super().render_to_response(context, **kwargs)
