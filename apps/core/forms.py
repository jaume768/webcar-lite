"""Widgets y campos de formulario comunes.

Los selectores de fecha y hora son los nativos del navegador (`type="date"` y
`type="datetime-local"`): accesibles con teclado, traducidos por el sistema
operativo y sin una libreria de calendario que mantener. A cambio exigen el
formato ISO, que es justo lo que fijan estas clases.
"""

from django import forms

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
