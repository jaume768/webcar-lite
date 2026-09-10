"""Formularios de cobros."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateField

from .models import PaymentMethod, PaymentType


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
