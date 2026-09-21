"""Formularios de configuracion."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CompanySettings, Policy, TermsVersion


class CompanySettingsForm(forms.ModelForm):
    # Django 6 pasa a asumir https; se fija ya para no depender del defecto.
    website = forms.URLField(label=_("Web"), required=False, assume_scheme="https")

    class Meta:
        model = CompanySettings
        fields = [
            "legal_name",
            "trade_name",
            "tax_id",
            "address",
            "city",
            "province",
            "postal_code",
            "country",
            "phone",
            "email",
            "website",
            "logo",
            "registry_note",
            "email_confirmation",
            "email_reminder",
            "reminder_hours",
            "email_contract",
            "email_return",
            "email_invoice",
            "pickup_instructions",
        ]
        widgets = {
            "registry_note": forms.Textarea(attrs={"rows": 2}),
            "pickup_instructions": forms.Textarea(attrs={"rows": 4}),
        }


class TermsVersionForm(forms.ModelForm):
    """Nueva version de las condiciones generales.

    Nunca edita una publicada: siempre crea otra. Es lo que permite que un
    contrato de hace dos anos siga diciendo lo que decia.
    """

    class Meta:
        model = TermsVersion
        fields = ["title", "body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 16, "placeholder": _("Una clausula por linea.")})
        }

    def clean_body(self):
        cuerpo = self.cleaned_data["body"].strip()
        if not cuerpo:
            raise forms.ValidationError(_("Las condiciones no pueden estar vacias."))
        return cuerpo


class PolicyForm(forms.ModelForm):
    class Meta:
        model = Policy
        fields = ["title", "body", "show_on_invoice", "sort_order"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6})}
