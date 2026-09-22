"""Validacion de lo que llega por la API. Los mismos formularios de siempre."""

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from apps.billing.models import OnlinePurpose
from apps.customers.forms import CustomerForm
from apps.customers.models import Customer


class IsoDateTimeField(forms.Field):
    """Fecha y hora ISO 8601. Sin zona horaria se entiende hora de Espana."""

    default_error_messages = {
        "invalid": _("Fecha y hora en formato ISO 8601, por ejemplo 2026-07-01T10:00:00+02:00."),
    }

    def to_python(self, value):
        if value in self.empty_values:
            return None
        momento = parse_datetime(str(value)) if not hasattr(value, "tzinfo") else value
        if momento is None:
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        if timezone.is_naive(momento):
            momento = timezone.make_aware(momento)
        return momento


class PeriodForm(forms.Form):
    pickup_office = forms.SlugField()
    return_office = forms.SlugField(required=False)
    pickup_at = IsoDateTimeField()
    return_at = IsoDateTimeField()

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("pickup_at"), datos.get("return_at")
        if inicio and fin and fin <= inicio:
            self.add_error("return_at", _("La devolución tiene que ser posterior a la recogida."))
        return datos


class BookingForm(PeriodForm):
    category = forms.SlugField()
    notes = forms.CharField(required=False, max_length=1000)
    external_ref = forms.CharField(required=False, max_length=80)


class ExtraLineForm(forms.Form):
    code = forms.SlugField()
    quantity = forms.IntegerField(min_value=1, max_value=20, initial=1, required=False)


class PaymentRequestForm(forms.Form):
    provider = forms.ChoiceField(choices=[("stripe", "Stripe"), ("redsys", "Redsys")])
    purpose = forms.ChoiceField(
        choices=[(OnlinePurpose.ADVANCE, ""), (OnlinePurpose.PAYMENT, "")],
        required=False,
    )
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)

    def clean(self):
        datos = super().clean()
        datos["purpose"] = datos.get("purpose") or OnlinePurpose.PAYMENT
        if datos["purpose"] == OnlinePurpose.ADVANCE and not datos.get("amount"):
            self.add_error("amount", _("Un anticipo necesita importe."))
        return datos


class ApiCustomerForm(CustomerForm):
    """El titular tal como lo manda la web.

    Correo, telefono y documento son obligatorios: sin ellos el mostrador no
    puede ni avisar al cliente ni hacer el parte de SES.Hospedajes.
    """

    class Meta(CustomerForm.Meta):
        fields = [
            "first_name",
            "last_name",
            "birth_date",
            "nationality",
            "sex",
            "language",
            "document_type",
            "document_number",
            "document_support",
            "document_expiry",
            "email",
            "phone",
            "address",
            "city",
            "province",
            "postal_code",
            "country",
            "licence_number",
            "licence_country",
            "licence_issued_on",
            "licence_expiry",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        obligatorios = self.OBLIGATORIOS
        for nombre, campo in self.fields.items():
            if nombre in obligatorios:
                campo.required = True
            elif nombre not in self.data:
                # Lo que no llega se queda con el valor por defecto del modelo
                # (nacionalidad y pais ES, idioma espanol).
                campo.required = False

    def clean(self):
        """Un documento repetido no es un error aqui: es un cliente que vuelve.

        Se valida contra su ficha para que la restriccion de documento unico no
        salte. El formulario no guarda nada: el servicio decide que se completa.
        """
        datos = super().clean()
        if not self.errors.get("document_number"):
            existente = Customer.objects.filter(
                document_type=datos.get("document_type"),
                document_number=datos.get("document_number"),
            ).first()
            if existente is not None:
                self.instance = existente
        return datos

    OBLIGATORIOS = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "document_type",
        "document_number",
    )
