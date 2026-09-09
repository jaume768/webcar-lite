"""Formularios de clientes."""

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DateInput

from .models import Customer, CustomerDocument
from .validators import validar_documento


class CustomerForm(forms.ModelForm):
    birth_date = forms.DateField(label=_("fecha de nacimiento"), required=False, widget=DateInput)
    document_expiry = forms.DateField(
        label=_("caducidad del documento"), required=False, widget=DateInput
    )
    licence_issued_on = forms.DateField(
        label=_("fecha de expedicion del carnet"), required=False, widget=DateInput
    )
    licence_expiry = forms.DateField(
        label=_("caducidad del carnet"), required=False, widget=DateInput
    )

    class Meta:
        model = Customer
        fields = [
            "first_name",
            "last_name",
            "birth_date",
            "nationality",
            "document_type",
            "document_number",
            "document_expiry",
            "email",
            "phone",
            "phone_alt",
            "address",
            "city",
            "province",
            "postal_code",
            "country",
            "licence_number",
            "licence_country",
            "licence_issued_on",
            "licence_expiry",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        """Valida el documento contra su tipo.

        Va en `clean()` y no en `clean_document_number()` porque hacen falta los
        dos campos: la misma cadena es un DNI valido y un NIE invalido.
        """
        datos = super().clean()
        tipo = datos.get("document_type")
        numero = datos.get("document_number")
        if tipo and numero:
            try:
                datos["document_number"] = validar_documento(tipo, numero)
            except ValidationError as exc:
                self.add_error("document_number", exc)
        return datos


class CustomerBlacklistForm(forms.Form):
    """Marca de cliente conflictivo. El motivo es obligatorio al marcar."""

    is_blacklisted = forms.BooleanField(label=_("Marcar como conflictivo"), required=False)
    blacklist_reason = forms.CharField(
        label=_("Motivo"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Lo lee quien atienda a este cliente en cualquier oficina."),
    )

    def clean(self):
        datos = super().clean()
        if datos.get("is_blacklisted") and not (datos.get("blacklist_reason") or "").strip():
            self.add_error("blacklist_reason", _("Explica por que se marca al cliente."))
        return datos


class CustomerDocumentForm(forms.ModelForm):
    """Subida de un escaneo. Va al almacen privado, nunca a una URL publica."""

    class Meta:
        model = CustomerDocument
        fields = ["kind", "file", "notes"]

    def clean_file(self):
        fichero = self.cleaned_data["file"]
        limite = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if fichero.size > limite:
            raise ValidationError(
                _("El fichero ocupa demasiado: el maximo son %(mb)s MB.")
                % {"mb": settings.MAX_UPLOAD_SIZE_MB}
            )

        permitidas = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
        nombre = (fichero.name or "").lower()
        if not any(nombre.endswith(ext) for ext in permitidas):
            raise ValidationError(
                _("Formato no admitido. Sube una imagen (JPG, PNG, WEBP, HEIC) o un PDF.")
            )
        return fichero


class CustomerChoiceField(forms.ModelChoiceField):
    """Selector de cliente para reservas.

    Solo clientes activos. Un cliente marcado como conflictivo **si** aparece:
    la marca es un aviso para quien atiende, no una prohibicion, y quien decide
    es el responsable de la oficina.
    """

    def __init__(self, *, queryset=None, **kwargs):
        kwargs.setdefault("label", _("Cliente"))
        kwargs.setdefault("empty_label", _("Busca un cliente"))
        super().__init__(queryset=queryset or Customer.objects.active(), **kwargs)

    def label_from_instance(self, obj):
        etiqueta = f"{obj.full_name} · {obj.document_number}"
        return f"⚠ {etiqueta}" if obj.is_blacklisted else etiqueta
