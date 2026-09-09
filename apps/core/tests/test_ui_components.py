"""Badges, etiquetas de plantilla y piezas de interfaz."""

import re
from pathlib import Path

import pytest
from django import forms
from django.conf import settings
from django.template import Context, Template
from django.test import RequestFactory

from apps.core import badges
from apps.core.templatetags.ui import add_class, is_current, widget_kind


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


def test_el_modal_nunca_se_sale_de_la_pantalla():
    """Regresion: un formulario largo desbordaba el modal y no habia scroll.

    El panel se limita al alto de la ventana y el que hace scroll es el cuerpo,
    con cabecera y pie fijos: sin esto, el boton de guardar quedaba cortado y el
    fondo esta bloqueado, asi que no habia forma de llegar a el.
    """
    plantilla = Template('{% extends "ui/_modal.html" %}{% block modal_body %}x{% endblock %}')

    html = plantilla.render(Context({}))

    assert "max-h-full" in html  # el panel no pasa del alto de la ventana
    assert "overflow-y-auto" in html  # y el cuerpo hace scroll por dentro
    assert "min-h-0" in html  # sin esto, un hijo flex se niega a encogerse


def test_las_plantillas_no_escupen_comentarios():
    """Regresion: `{# ... #}` en varias lineas no es un comentario para Django.

    Se imprimia tal cual en pantalla. Los comentarios largos van con
    {% templatetag openblock %} comment {% templatetag closeblock %}.
    """
    plantillas = Path(settings.BASE_DIR, "apps").rglob("*.html")

    sospechosas = [
        f"{ruta}:{numero}"
        for ruta in plantillas
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if "{#" in linea and "#}" not in linea
    ]

    assert sospechosas == []


class FormularioConGrupos(forms.Form):
    """Los tres tipos de campo que pinta ui/_field.html de forma distinta."""

    nombre = forms.CharField()
    activo = forms.BooleanField(required=False, label="Activo")
    categorias = forms.MultipleChoiceField(
        choices=[("eco", "Economico"), ("suv", "SUV")],
        widget=forms.CheckboxSelectMultiple,
        label="Categorias",
        help_text="A que categorias se aplica",
    )


def _pintar(field):
    plantilla = Template('{% include "ui/_field.html" %}')
    return plantilla.render(Context({"field": field}))


def test_widget_kind_distingue_las_tres_formas():
    formulario = FormularioConGrupos()

    assert widget_kind(formulario["nombre"]) == "input"
    assert widget_kind(formulario["activo"]) == "checkbox"
    assert widget_kind(formulario["categorias"]) == "choices"


def test_un_grupo_de_casillas_no_mete_las_opciones_dentro_de_la_etiqueta():
    """Regresion: las opciones salian pisando el texto de la etiqueta.

    `CheckboxSelectMultiple` tambien dice `input_type == "checkbox"`, asi que
    entraba por la rama del checkbox suelto y el widget entero acababa dentro
    del <label>. Ahora es un fieldset con su legend y las opciones debajo.
    """
    html = _pintar(FormularioConGrupos()["categorias"])

    assert "<fieldset" in html
    assert "<legend" in html
    assert 'class="choice-group"' in html
    # La etiqueta va antes que las opciones, no envolviendolas.
    assert html.index("Categorias") < html.index("Economico")
    assert not re.search(r"<label[^>]*>\s*<div", html)


def test_un_checkbox_suelto_sigue_con_la_etiqueta_al_lado():
    html = _pintar(FormularioConGrupos()["activo"])

    assert "<fieldset" not in html
    assert 'type="checkbox"' in html
    assert html.index('type="checkbox"') < html.index("Activo")


def test_el_grupo_no_recibe_las_clases_de_la_casilla():
    """`size-4` en el contenedor dejaria el grupo entero en 16x16 px."""
    html = _pintar(FormularioConGrupos()["categorias"])

    assert 'id="id_categorias" class=' not in html


def test_el_grupo_describe_su_ayuda_para_los_lectores_de_pantalla():
    html = _pintar(FormularioConGrupos()["categorias"])

    assert 'aria-describedby="id_categorias-ayuda"' in html
    assert 'id="id_categorias-ayuda"' in html
