"""Formularios de acceso y de gestion de usuarios."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from apps.offices.models import Office
from apps.offices.selectors import offices_for_user

from .models import Role, User


class EmailAuthenticationForm(AuthenticationForm):
    """Login por correo. El campo sigue llamandose `username` por compatibilidad
    con Django y con django-axes, pero al usuario se le pide su correo."""

    username = forms.EmailField(
        label=_("Correo electronico"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Correo o contrasena incorrectos."),
        "inactive": _("Esta cuenta esta desactivada."),
    }


class OfficeScopedFormMixin:
    """Limita el campo de oficina a las del usuario que rellena el formulario.

    No es cosmetica: recortar el queryset hace que un POST con el id de otra
    oficina falle la validacion, que es donde de verdad se corta el intento.
    """

    office_fields: tuple[str, ...] = ("office",)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        permitidas = offices_for_user(user)
        for nombre in self.office_fields:
            if nombre in self.fields:
                self.fields[nombre].queryset = permitidas


class UserForm(OfficeScopedFormMixin, forms.ModelForm):
    """Alta y edicion de usuarios desde el panel de gestion."""

    office_fields = ("offices",)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "phone", "role", "offices"]
        widgets = {
            "offices": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.order_by("name")
        self.fields["role"].empty_label = _("Sin rol")

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        existente = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            existente = existente.exclude(pk=self.instance.pk)
        if existente.exists():
            raise forms.ValidationError(_("Ya hay un usuario con ese correo."))
        return email

    def clean_offices(self):
        """Segunda barrera: el queryset ya recorta, pero esto lo deja explicito."""
        oficinas = self.cleaned_data["offices"]
        permitidas = set(offices_for_user(self.user).values_list("pk", flat=True))
        fuera = [o for o in oficinas if o.pk not in permitidas]
        if fuera:
            raise forms.ValidationError(
                _("No puedes asignar oficinas que no gestionas: %(oficinas)s"),
                params={"oficinas": ", ".join(o.name for o in fuera)},
                code="oficina_fuera_de_alcance",
            )
        return oficinas


class OfficeForm(forms.ModelForm):
    class Meta:
        model = Office
        fields = [
            "code",
            "name",
            "address",
            "city",
            "province",
            "postal_code",
            "country",
            "phone",
            "email",
        ]
