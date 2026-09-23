"""Widgets y campos de formulario comunes.

Los selectores de fecha y hora son los nativos del navegador (`type="date"` y
`type="datetime-local"`): accesibles con teclado, traducidos por el sistema
operativo y sin una libreria de calendario que mantener. A cambio exigen el
formato ISO, que es justo lo que fijan estas clases.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Lead

FORMATO_FECHA = "%Y-%m-%d"
FORMATO_FECHA_HORA = "%Y-%m-%dT%H:%M"


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs=attrs, format=format or FORMATO_FECHA)


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None, format=None):
        # step=900: el mostrador trabaja en cuartos de hora.
        atributos = {"step": 900}
        atributos.update(attrs or {})
        super().__init__(attrs=atributos, format=format or FORMATO_FECHA_HORA)


class DateField(forms.DateField):
    widget = DateInput

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", [FORMATO_FECHA])
        super().__init__(**kwargs)


class DateTimeField(forms.DateTimeField):
    widget = DateTimeInput

    def __init__(self, **kwargs):
        # El navegador manda "2026-04-12T09:30"; el formato con segundos aparece
        # cuando el usuario teclea la hora a mano en algunos navegadores.
        kwargs.setdefault("input_formats", [FORMATO_FECHA_HORA, "%Y-%m-%dT%H:%M:%S"])
        super().__init__(**kwargs)


class LeadForm(forms.Form):
    """Formulario de contacto de la portada.

    Lo rellena gente de fuera, sin sesion: se pide lo justo (quien eres y por
    donde te llamamos) y se exige al menos una forma de contacto. Todo lo demas
    se pregunta por telefono.
    """

    name = forms.CharField(label=_("Tu nombre"), max_length=120)
    company = forms.CharField(label=_("Empresa"), max_length=160, required=False)
    phone = forms.CharField(label=_("Telefono o WhatsApp"), max_length=32, required=False)
    email = forms.EmailField(label=_("Correo"), required=False)
    fleet_size = forms.IntegerField(
        label=_("Vehiculos en flota"), required=False, min_value=1, max_value=10_000
    )
    message = forms.CharField(
        label=_("Cuentanos"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    #: Trampa para robots: un campo que una persona no ve y no rellena. Si
    #: viene con algo, el envio se descarta sin decir por que.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
        label=_("No rellenar"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La portada no usa `ui/_field.html` (no carga el shell): los estilos
        # del campo se ponen aqui, una vez, en lugar de en cada `<input>`.
        for nombre, campo in self.fields.items():
            if nombre == "website":
                continue
            campo.widget.attrs.setdefault("class", "input mt-1")
        self.fields["phone"].widget.attrs.setdefault("inputmode", "tel")
        self.fields["email"].widget.attrs.setdefault("inputmode", "email")
        self.fields["fleet_size"].widget.attrs.setdefault("inputmode", "numeric")

    def clean(self):
        datos = super().clean()
        if not datos.get("phone") and not datos.get("email"):
            raise forms.ValidationError(
                _("Dejanos un telefono o un correo: si no, no podemos contestarte.")
            )
        return datos

    @property
    def is_bot(self) -> bool:
        return bool(self.cleaned_data.get("website"))


class LeadFollowUpForm(forms.ModelForm):
    """Lo unico que el equipo edita de un contacto: en que punto esta."""

    class Meta:
        model = Lead
        fields = ["status", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
