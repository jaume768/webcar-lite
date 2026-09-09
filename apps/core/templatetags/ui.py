"""Etiquetas de plantilla del sistema de interfaz."""

from django import template
from django.urls import resolve

from ..badges import classes_for

register = template.Library()


@register.simple_tag(name="badge_classes")
def badge_classes(status: str) -> str:
    """Clases del badge segun el estado. Ver core.badges."""
    return classes_for(status)


@register.simple_tag(name="is_current")
def is_current(request, url: str) -> bool:
    """True si `url` corresponde a la vista que se esta mostrando.

    Compara por vista resuelta, no por prefijo de ruta: asi "/" no marca como
    activa toda la aplicacion.
    """
    if not request or not url:
        return False
    try:
        actual = resolve(request.path_info)
        candidata = resolve(url)
    except Exception:  # una URL que no resuelve simplemente no esta activa
        return False
    return actual.view_name == candidata.view_name


@register.filter(name="add_class")
def add_class(field, css: str):
    """Renderiza un BoundField con las clases dadas y el estado de error.

    Anade `input-invalid`, `aria-invalid` y `aria-describedby` cuando el campo
    trae errores, para que el fallo se vea y tambien se anuncie.
    """
    attrs = {"class": css}
    if getattr(field, "errors", None):
        attrs["class"] = f"{css} input-invalid"
        attrs["aria-invalid"] = "true"
    if getattr(field, "help_text", ""):
        attrs["aria-describedby"] = f"{field.auto_id}-ayuda"
    return field.as_widget(attrs=attrs)
