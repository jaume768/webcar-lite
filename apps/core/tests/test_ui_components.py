"""Badges y etiquetas de plantilla."""

import pytest
from django import forms
from django.template import Context, Template
from django.test import RequestFactory

from apps.core import badges
from apps.core.templatetags.ui import add_class, is_current


def test_el_tono_por_defecto_es_neutro():
    assert badges.tone_for("estado-que-no-existe") == badges.DEFAULT_TONE
    assert badges.classes_for("estado-que-no-existe") == badges.TONES["neutral"]


def test_registrar_un_estado_con_tono_valido():
    badges.register("prueba-tono", "success")
    try:
        assert badges.tone_for("prueba-tono") == "success"
    finally:
        badges._STATUS_TONES.pop("prueba-tono", None)


def test_registrar_un_tono_inexistente_falla():
    """Mejor romper al arrancar que pintar un badge sin color."""
    with pytest.raises(KeyError):
        badges.register("prueba-tono", "fucsia")


def test_is_current_compara_por_vista_no_por_prefijo():
    peticion = RequestFactory().get("/ui-kit/")

    assert is_current(peticion, "/ui-kit/") is True
    # "/" no puede marcar como activa toda la aplicacion.
    assert is_current(peticion, "/") is False


def test_is_current_tolera_urls_que_no_resuelven():
    peticion = RequestFactory().get("/ui-kit/")

    assert is_current(peticion, "/no-existe/") is False
    assert is_current(peticion, "") is False
    assert is_current(None, "/ui-kit/") is False


class FormularioDePrueba(forms.Form):
    nombre = forms.CharField(help_text="Como aparece en el contrato")


def test_add_class_marca_los_campos_con_error():
    form = FormularioDePrueba(data={"nombre": ""})
    assert not form.is_valid()

    html = str(add_class(form["nombre"], "input"))

    assert 'class="input input-invalid"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="id_nombre-ayuda"' in html


def test_add_class_sin_errores_no_marca_nada():
    html = str(add_class(FormularioDePrueba()["nombre"], "input"))

    assert 'class="input"' in html
    assert "aria-invalid" not in html


def test_el_badge_pinta_el_color_del_estado():
    plantilla = Template('{% include "ui/_badge.html" with status="active" label="Activo" only %}')

    html = plantilla.render(Context({}))

    assert "Activo" in html
    assert badges.TONES["success"] in html
