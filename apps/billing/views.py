"""Pantallas de facturacion: facturas, cobros, arqueo de caja y series."""

import structlog
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from apps.core.crud import (
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
from apps.reservations.models import Reservation

from .filters import (
    InvoiceFilter,
    InvoiceSeriesFilter,
    OnlinePaymentFilter,
    PaymentFilter,
    ToInvoiceFilter,
)
from .forms import (
    CaptureDepositForm,
    CashRegisterForm,
    InvoiceSeriesForm,
    IssueInvoiceForm,
    ManualInvoiceForm,
    ManualInvoiceLineFormSet,
    OnlinePaymentForm,
    PaymentForm,
    RectifyInvoiceForm,
)
from .models import Invoice, InvoiceSeries, OnlinePayment, Payment
from .pdf import render_invoice_pdf
from .selectors import (
    cash_register,
    cash_totals_by_method,
    pending_amount,
    reservations_to_invoice,
)
from .services import (
    OverpaymentNotAllowed,
    invoice_lines_for,
    issue_invoice,
    rectify_invoice,
    refund,
    register_payment,
    save_series,
    set_series_active,
    totals_of,
)

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


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------


class ToInvoiceListView(CrudListView):
    """Reservas finalizadas que aun no tienen factura en vigor."""

    permission_required = "billing.view_billing"
    model = Reservation
    filterset_class = ToInvoiceFilter
    table_id = "tabla-por-facturar"
    table_row_template = "billing/_to_invoice_row.html"
    table_columns = [
        Column(label=_("Reserva"), css="w-36"),
        Column(label=_("Cliente")),
        Column(label=_("Devolución")),
        Column(label=_("Oficina")),
        Column(label=_("Total"), align="right"),
        Column(label=_("Acción"), align="right"),
    ]
    search_placeholder = _("Reserva, cliente o matrícula...")
    empty_title = _("Todo facturado")
    empty_message = _("No queda ninguna reserva finalizada sin su factura.")
    page_title = _("Pendiente de facturar")

    def get_base_queryset(self):
        return reservations_to_invoice(self.request.user)


class InvoiceListView(CrudListView):
    permission_required = "billing.view_billing"
    model = Invoice
    scope_to_user = True
    filterset_class = InvoiceFilter
    table_id = "tabla-facturas"
    table_row_template = "billing/_invoice_row.html"
    table_columns = [
        Column(label=_("Número"), css="w-36"),
        Column(label=_("Fecha")),
        Column(label=_("Cliente")),
        Column(label=_("Reserva")),
        Column(label=_("Base"), align="right"),
        Column(label=_("IVA"), align="right"),
        Column(label=_("Total"), align="right"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Factura, reserva, cliente o NIF...")
    create_url_name = "billing:manual_invoice"
    create_label = _("Factura libre")
    create_permission = "billing.add_invoice"
    create_in_modal = False
    empty_title = _("Ninguna factura coincide")
    empty_message = _("Cambia la búsqueda o quita algún filtro.")
    page_title = _("Facturas")

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .select_related("series", "reservation", "rectifies")
            .prefetch_related("rectifications")
        )


class InvoiceScopedMixin(CrudPermissionMixin):
    """Una factura de otra oficina no existe para este usuario, ni por URL."""

    permission_required = "billing.view_billing"

    def get_invoice(self) -> Invoice:
        if not hasattr(self, "_factura"):
            self._factura = get_object_or_404(
                Invoice.objects.for_user(self.request.user).select_related(
                    "series", "reservation", "office", "rectifies", "created_by"
                ),
                pk=self.kwargs["pk"],
            )
        return self._factura


class InvoiceDetailView(InvoiceScopedMixin, TemplateView):
    template_name = "billing/invoice_detail.html"

    def get_context_data(self, **kwargs):
        from apps.settings_app.models import CompanySettings

        from .verifactu import qr_svg

        factura = self.get_invoice()
        empresa = CompanySettings.load()
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            {
                "invoice": factura,
                "lineas": factura.lines.all(),
                "rectificativas": factura.rectifications.all(),
                "qr": qr_svg(factura.qr_data),
                "logo_url": empresa.logo.url if empresa.logo else "",
                "page_title": str(factura),
                "breadcrumbs": [
                    {"label": _("Inicio"), "url": reverse("core:home")},
                    {"label": _("Facturas"), "url": reverse("billing:invoice_list")},
                    {"label": factura.number},
                ],
            }
        )
        return contexto


class InvoicePdfView(InvoiceScopedMixin, View):
    """El PDF se genera al pedirlo: la factura no cambia, asi que sale siempre igual.

    Lo puede sacar quien vea facturacion o quien vea la reserva: en mostrador
    hay que poder imprimirle la factura al cliente. El scope de oficina se
    sigue aplicando en la consulta.
    """

    PERMISOS = ("billing.view_billing", "reservations.view_reservation")

    def has_permission(self):
        usuario = self.request.user
        return any(usuario.has_perm(permiso) for permiso in self.PERMISOS)

    def get(self, request, *args, **kwargs):
        factura = self.get_invoice()
        contenido = render_invoice_pdf(factura)
        logger.info("factura_descargada", invoice_number=factura.number, actor_id=request.user.pk)
        respuesta = HttpResponse(contenido, content_type="application/pdf")
        respuesta["Content-Disposition"] = f'inline; filename="{factura.number}.pdf"'
        return respuesta


class InvoiceIssueView(ModalFormView):
    """Emitir la factura de una reserva, con la vista previa de sus lineas."""

    permission_required = "billing.add_invoice"
    form_class = IssueInvoiceForm
    modal_title = _("Emitir factura")
    submit_label = _("Emitir factura")
    list_url_name = "billing:to_invoice"
    table_id = "emitir-factura"
    modal_width = "max-w-2xl"

    @property
    def reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related("customer"),
                pk=self.kwargs["pk"],
            )
        return self._reserva

    def get_template_names(self):
        if self.request.htmx:
            return ["billing/_invoice_issue_modal.html"]
        return ["ui/_form_page.html"]

    def get_context_data(self, **kwargs):
        lineas = invoice_lines_for(self.reservation)
        base, cuota, total = totals_of(lineas)
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            {
                "reservation": self.reservation,
                "lineas": lineas,
                "base": base,
                "cuota": cuota,
                "total": total,
            }
        )
        return contexto

    def save_object(self, form):
        return issue_invoice(
            reservation=self.reservation,
            series=form.cleaned_data["series"],
            actor=self.request.user,
        )

    def get_success_message(self, objeto) -> str:
        return _("Factura %(numero)s emitida.") % {"numero": objeto.number}

    def get_success_url(self) -> str:
        return reverse("billing:invoice_detail", args=[self.object.pk])

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if self.request.htmx and respuesta.status_code == 200:
            trigger_event(respuesta, EVENTO_RESERVA)
        return respuesta


class InvoiceRectifyView(InvoiceScopedMixin, ModalFormView):
    """Anular una factura con su rectificativa y abrir la rectificativa."""

    permission_required = "billing.rectify_invoice"
    form_class = RectifyInvoiceForm
    modal_title = _("Emitir rectificativa")
    submit_label = _("Emitir rectificativa")
    list_url_name = "billing:invoice_list"
    table_id = "rectificar-factura"

    def get_template_names(self):
        if self.request.htmx:
            return ["billing/_invoice_rectify_modal.html"]
        return ["ui/_form_page.html"]

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["invoice"] = self.get_invoice()
        return contexto

    def save_object(self, form):
        return rectify_invoice(
            invoice=self.get_invoice(),
            reason=form.cleaned_data["reason"],
            series=form.cleaned_data["series"],
            actor=self.request.user,
        )

    def get_success_message(self, objeto) -> str:
        return _("Rectificativa %(numero)s emitida.") % {"numero": objeto.number}

    def get_success_url(self) -> str:
        return reverse("billing:invoice_detail", args=[self.object.pk])

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if self.request.htmx and respuesta.status_code == 200 and getattr(self, "object", None):
            # La ficha de la original ya no vale: se abre la rectificativa.
            respuesta["HX-Redirect"] = self.get_success_url()
            messages.success(self.request, self.get_success_message(self.object))
        return respuesta


# ---------------------------------------------------------------------------
# Cobros
# ---------------------------------------------------------------------------


class PaymentListView(CrudListView):
    """Todos los movimientos de dinero, de todas las reservas del usuario."""

    permission_required = "billing.view_billing"
    model = Payment
    scope_to_user = True
    filterset_class = PaymentFilter
    table_id = "tabla-cobros"
    table_row_template = "billing/_payment_row.html"
    table_columns = [
        Column(label=_("Fecha")),
        Column(label=_("Reserva")),
        Column(label=_("Cliente")),
        Column(label=_("Concepto")),
        Column(label=_("Medio")),
        Column(label=_("Referencia")),
        Column(label=_("Oficina")),
        Column(label=_("Importe"), align="right"),
    ]
    search_placeholder = _("Reserva, referencia o cliente...")
    empty_title = _("Ningún cobro coincide")
    empty_message = _("Cambia la búsqueda o quita algún filtro.")
    page_title = _("Cobros")

    def get_base_queryset(self):
        return (
            super()
            .get_base_queryset()
            .select_related("reservation__customer", "office", "created_by")
        )


# ---------------------------------------------------------------------------
# Series (administracion)
# ---------------------------------------------------------------------------


class InvoiceSeriesListView(CrudListView):
    permission_required = "billing.view_invoiceseries"
    model = InvoiceSeries
    filterset_class = InvoiceSeriesFilter
    table_id = "tabla-series"
    table_row_template = "billing/_series_row.html"
    table_columns = [
        Column(label=_("Código"), css="font-mono tabular w-24"),
        Column(label=_("Nombre")),
        Column(label=_("Tipo")),
        Column(label=_("Formato")),
        Column(label=_("Siguiente")),
        Column(label=_("Emitidas"), align="right"),
        Column(label=_("Estado")),
        Column(label=_("Acciones"), align="right"),
    ]
    search_placeholder = _("Código o nombre...")
    empty_title = _("Ninguna serie coincide")
    empty_message = _("Cambia la búsqueda o quita algún filtro.")
    page_title = _("Series de facturación")
    create_url_name = "billing:series_create"
    create_label = _("Nueva serie")
    create_permission = "billing.add_invoiceseries"

    def get_base_queryset(self):
        from django.db.models import Count

        return (
            super()
            .get_base_queryset()
            .annotate(emitidas=Count("invoices"))
            .prefetch_related("counters")
            .order_by("kind", "code")
        )


class InvoiceSeriesFormMixin:
    model = InvoiceSeries
    form_class = InvoiceSeriesForm
    table_id = "tabla-series"
    list_url_name = "billing:series_list"

    def save_object(self, form):
        return save_series(series=form.save(commit=False), actor=self.request.user)


class InvoiceSeriesCreateView(InvoiceSeriesFormMixin, ModalCreateView):
    permission_required = "billing.add_invoiceseries"
    modal_title = _("Nueva serie")
    submit_label = _("Crear serie")
    success_message = _("Serie %(objeto)s creada.")


class InvoiceSeriesUpdateView(InvoiceSeriesFormMixin, ModalUpdateView):
    permission_required = "billing.change_invoiceseries"
    modal_title = _("Editar serie")
    success_message = _("Serie %(objeto)s actualizada.")


class InvoiceSeriesToggleView(ToggleActiveView):
    permission_required = "billing.change_invoiceseries"
    model = InvoiceSeries
    list_url_name = "billing:series_list"
    activated_message = _("Serie %(objeto)s reactivada.")
    deactivated_message = _("Serie %(objeto)s desactivada.")

    def perform(self, objeto):
        set_series_active(series=objeto, active=self.activate, actor=self.request.user)


class InvoiceSeriesActivateView(InvoiceSeriesToggleView):
    activate = True


class InvoiceSeriesDeactivateView(InvoiceSeriesToggleView):
    activate = False


# ---------------------------------------------------------------------------
# Factura libre
# ---------------------------------------------------------------------------


class ManualInvoiceCreateView(CrudPermissionMixin, TemplateView):
    """Factura a un cliente sin reserva: una multa, un dano, un cargo suelto.

    Acepta valores de partida por la URL (`customer`, `concept`, `price`,
    `tax`, `office`, `notes`, `multa`) para que otras pantallas, como la ficha
    de una multa, abran el formulario ya relleno.
    """

    permission_required = "billing.add_invoice"
    template_name = "billing/manual_invoice.html"

    def _iniciales(self):
        from django.conf import settings

        datos = self.request.GET
        cabecera = {
            clave: datos[clave] for clave in ("customer", "office", "notes") if datos.get(clave)
        }
        linea = {"quantity": 1, "tax_rate": settings.DEFAULT_TAX_RATE}
        if datos.get("concept"):
            linea["concept"] = datos["concept"]
        if datos.get("price"):
            linea["unit_price"] = datos["price"]
        if datos.get("tax"):
            linea["tax_rate"] = datos["tax"]
        return cabecera, [linea]

    def get(self, request, *args, **kwargs):
        cabecera, lineas = self._iniciales()
        return self.render_to_response(
            self.contexto(
                ManualInvoiceForm(initial=cabecera, user=request.user),
                ManualInvoiceLineFormSet(initial=lineas, prefix="lineas"),
            )
        )

    def post(self, request, *args, **kwargs):
        from .services import free_line, issue_manual_invoice

        form = ManualInvoiceForm(request.POST, user=request.user)
        lineas = ManualInvoiceLineFormSet(request.POST, prefix="lineas")
        if not (form.is_valid() and lineas.is_valid()):
            return self.render_to_response(self.contexto(form, lineas), status=422)
        datos = form.cleaned_data
        try:
            factura = issue_manual_invoice(
                customer=datos["customer"],
                office=datos["office"],
                series=datos["series"],
                notes=datos["notes"],
                lines=[
                    free_line(
                        concept=linea["concept"],
                        quantity=linea["quantity"],
                        unit_price=linea["unit_price"],
                        tax_rate=linea["tax_rate"],
                    )
                    for linea in lineas.cleaned_data
                    if linea and linea.get("concept")
                ],
                actor=request.user,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
            return self.render_to_response(self.contexto(form, lineas), status=422)

        if request.GET.get("multa"):
            from apps.operations.fines import attach_invoice

            attach_invoice(fine_id=request.GET["multa"], invoice=factura, actor=request.user)
        messages.success(request, _("Factura %(numero)s emitida.") % {"numero": factura.number})
        return HttpResponseRedirect(reverse("billing:invoice_detail", args=[factura.pk]))

    def contexto(self, form, lineas):
        from apps.customers.models import Customer

        cliente = None
        valor = form["customer"].value()
        if valor:
            cliente = Customer.objects.filter(pk=valor).first()
        return {
            "form": form,
            "lineas": lineas,
            "cliente": cliente,
            "page_title": _("Nueva factura libre"),
            "breadcrumbs": [
                {"label": _("Inicio"), "url": reverse("core:home")},
                {"label": _("Facturas"), "url": reverse("billing:invoice_list")},
                {"label": _("Nueva factura libre")},
            ],
        }


# ---------------------------------------------------------------------------
# Pagos online: mostrador
# ---------------------------------------------------------------------------


class OnlinePaymentCreateView(ModalFormView):
    permission_required = "billing.add_onlinepayment"
    form_class = OnlinePaymentForm
    modal_title = _("Pedir pago online")
    submit_label = _("Crear enlace de pago")
    list_url_name = "reservations:list"
    table_id = "pago-online"

    @property
    def reservation(self) -> Reservation:
        if not hasattr(self, "_reserva"):
            self._reserva = get_object_or_404(
                Reservation.objects.for_user(self.request.user).select_related("customer"),
                pk=self.kwargs["pk"],
            )
        return self._reserva

    def get_initial(self):
        return {"amount": pending_amount(self.reservation) or self.reservation.deposit_amount}

    def get_context_data(self, **kwargs):
        from .gateways import enabled_providers

        contexto = super().get_context_data(**kwargs)
        contexto["sin_pasarelas"] = not enabled_providers()
        return contexto

    def get_template_names(self):
        if self.request.htmx:
            return ["billing/_online_payment_modal.html"]
        return ["ui/_form_page.html"]

    def save_object(self, form):
        from .online import create_link

        datos = form.cleaned_data
        pago = create_link(
            reservation=self.reservation,
            provider=datos["provider"],
            purpose=datos["purpose"],
            amount=datos["amount"],
            actor=self.request.user,
        )
        if datos["send_email"]:
            from apps.notifications.services import queue_email

            queue_email(
                kind="payment_link",
                reservation=self.reservation,
                context={"pago_id": pago.pk},
                actor=self.request.user,
            )
        return pago

    def get_success_message(self, objeto) -> str:
        return _("Enlace creado: %(url)s") % {"url": objeto.public_url}

    def get_success_url(self) -> str:
        return reverse("reservations:detail", args=[self.reservation.pk])

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if self.request.htmx and respuesta.status_code == 200:
            trigger_event(respuesta, EVENTO_RESERVA)
        return respuesta


class OnlinePaymentScopedMixin(CrudPermissionMixin):
    permission_required = "billing.change_onlinepayment"

    def get_online_payment(self):
        return get_object_or_404(
            OnlinePayment.objects.for_user(self.request.user).select_related("reservation"),
            pk=self.kwargs["pk"],
        )


class CaptureDepositView(OnlinePaymentScopedMixin, ModalFormView):
    form_class = CaptureDepositForm
    modal_title = _("Cobrar de la fianza")
    submit_label = _("Cobrar")
    list_url_name = "reservations:list"
    table_id = "cobrar-fianza"

    def get_initial(self):
        return {"amount": self.get_online_payment().amount}

    def save_object(self, form):
        from .online import capture_deposit

        return capture_deposit(
            online_payment=self.get_online_payment(),
            amount=form.cleaned_data["amount"],
            actor=self.request.user,
        )

    def get_success_message(self, objeto) -> str:
        return _("Cobrados %(importe)s € de la fianza.") % {"importe": objeto.captured_amount}

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if self.request.htmx and respuesta.status_code == 200:
            trigger_event(respuesta, EVENTO_RESERVA)
        return respuesta


class OnlinePaymentActionView(OnlinePaymentScopedMixin, View):
    """Liberar la fianza o anular un enlace. Por POST, respuesta HTMX."""

    def post(self, request, pk, accion, *args, **kwargs):
        from .online import cancel_link, release_deposit

        acciones = {
            "liberar": (release_deposit, _("Fianza liberada.")),
            "anular": (cancel_link, _("Enlace anulado.")),
        }
        if accion not in acciones:
            raise PermissionDenied
        servicio, mensaje = acciones[accion]
        try:
            servicio(online_payment=self.get_online_payment(), actor=request.user)
        except ServiceError as exc:
            return trigger_toast(HttpResponse(status=200), str(exc), "warning")
        respuesta = HttpResponse(status=200)
        trigger_event(respuesta, EVENTO_RESERVA)
        return trigger_toast(respuesta, mensaje, "success")


class OnlinePaymentListView(CrudListView):
    permission_required = "billing.view_onlinepayment"
    model = OnlinePayment
    filterset_class = OnlinePaymentFilter
    table_id = "tabla-pagos-online"
    table_row_template = "billing/_online_payment_row.html"
    table_columns = [
        Column(label=_("Creado")),
        Column(label=_("Reserva")),
        Column(label=_("Concepto")),
        Column(label=_("Pasarela")),
        Column(label=_("Importe"), align="right"),
        Column(label=_("Estado")),
    ]
    search_placeholder = _("Reserva o referencia...")
    empty_title = _("Sin pagos online")
    empty_message = _("Se piden desde la pestaña Cobros de cada reserva.")
    page_title = _("Pagos online")

    def get_base_queryset(self):
        return OnlinePayment.objects.for_user(self.request.user).select_related(
            "reservation__customer"
        )
