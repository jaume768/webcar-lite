"""Formularios de cobros, facturas y series."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateField

from .models import InvoiceKind, InvoiceSeries, PaymentMethod, PaymentType


class PaymentForm(forms.Form):
    """Registrar un movimiento de dinero de una reserva."""

    amount = forms.DecimalField(
        label=_("Importe"),
        min_value=0.01,
        max_digits=10,
        decimal_places=2,
        help_text=_("Siempre en positivo: el concepto decide si entra o sale."),
    )
    method = forms.ChoiceField(
        label=_("Medio de pago"), choices=PaymentMethod.choices, initial=PaymentMethod.CARD
    )
    payment_type = forms.ChoiceField(
        label=_("Concepto"), choices=PaymentType.choices, initial=PaymentType.PAYMENT
    )
    reference = forms.CharField(label=_("Referencia"), max_length=60, required=False)
    notes = forms.CharField(
        label=_("Notas"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class CashRegisterForm(forms.Form):
    """Filtro del arqueo: una oficina y un dia."""

    day = DateField(label=_("Dia"))
    office = forms.ModelChoiceField(label=_("Oficina"), queryset=None, empty_label=None)

    def __init__(self, *args, user=None, **kwargs):
        from apps.offices.selectors import offices_for_user

        super().__init__(*args, **kwargs)
        self.fields["office"].queryset = offices_for_user(user)


class IssueInvoiceForm(forms.Form):
    """Emitir la factura de una reserva: solo se elige la serie."""

    series = forms.ModelChoiceField(label=_("Serie"), queryset=None, empty_label=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        series = InvoiceSeries.objects.active().filter(kind=InvoiceKind.ORDINARY)
        self.fields["series"].queryset = series.order_by("-is_default", "code")
        por_defecto = series.filter(is_default=True).first()
        if por_defecto is not None:
            self.fields["series"].initial = por_defecto.pk


class RectifyInvoiceForm(forms.Form):
    """Anular una factura con su rectificativa. El motivo queda escrito en ella."""

    reason = forms.CharField(
        label=_("Motivo"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Sale impreso en la rectificativa. Ejemplo: datos del cliente erroneos."),
    )
    series = forms.ModelChoiceField(label=_("Serie"), queryset=None, empty_label=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        series = InvoiceSeries.objects.active().filter(kind=InvoiceKind.RECTIFYING)
        self.fields["series"].queryset = series.order_by("-is_default", "code")
        por_defecto = series.filter(is_default=True).first()
        if por_defecto is not None:
            self.fields["series"].initial = por_defecto.pk


class InvoiceSeriesForm(forms.ModelForm):
    """Alta y edicion de una serie. Activar y desactivar va por su propia accion."""

    class Meta:
        model = InvoiceSeries
        fields = ["code", "name", "kind", "number_format", "is_default", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean_number_format(self):
        from .services import InvoiceServiceError, validate_number_format

        try:
            return validate_number_format(self.cleaned_data["number_format"])
        except InvoiceServiceError as exc:
            raise forms.ValidationError(str(exc)) from None
