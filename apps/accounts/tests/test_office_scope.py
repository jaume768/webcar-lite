"""Aislamiento por oficina: por queryset, por vista y por formulario."""

import pytest
from django.http import Http404
from django.test import RequestFactory
from django.urls import reverse
from django.views.generic import DetailView

from apps.accounts.mixins import OfficeScopedMixin
from apps.core.tests.testapp.models import ScopedWidget

from .factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def widget_centro(centro):
    return ScopedWidget.objects.create(name="del centro", office=centro)


@pytest.fixture
def widget_norte(norte):
    return ScopedWidget.objects.create(name="del norte", office=norte)


# --- queryset ---------------------------------------------------------------


def test_for_user_solo_devuelve_las_oficinas_del_usuario(
    agente_centro, widget_centro, widget_norte
):
    visibles = ScopedWidget.objects.for_user(agente_centro)

    assert list(visibles) == [widget_centro]


def test_for_user_de_un_superusuario_lo_ve_todo(superusuario, widget_centro, widget_norte):
    assert ScopedWidget.objects.for_user(superusuario).count() == 2


def test_for_user_sin_usuario_no_devuelve_nada(widget_centro):
    from django.contrib.auth.models import AnonymousUser

    assert ScopedWidget.objects.for_user(None).count() == 0
    assert ScopedWidget.objects.for_user(AnonymousUser()).count() == 0


def test_for_user_de_un_usuario_desactivado_no_devuelve_nada(agente_centro, widget_centro):
    agente_centro.is_active = False
    agente_centro.save(update_fields=["is_active"])

    assert ScopedWidget.objects.for_user(agente_centro).count() == 0


def test_un_usuario_sin_oficinas_no_ve_nada(widget_centro, rol_mostrador):
    huerfano = UserFactory(email="sinoficina@ejemplo.es", role=rol_mostrador)

    assert ScopedWidget.objects.for_user(huerfano).count() == 0


# --- vista ------------------------------------------------------------------


class WidgetDetailView(OfficeScopedMixin, DetailView):
    model = ScopedWidget


def _pedir_detalle(usuario, widget):
    peticion = RequestFactory().get("/da-igual/")
    peticion.user = usuario
    return WidgetDetailView.as_view()(peticion, pk=widget.pk)


def test_pedir_por_url_algo_de_otra_oficina_da_404(agente_centro, widget_norte):
    """404 y no 403: un 403 confirmaria que el registro existe."""
    with pytest.raises(Http404):
        _pedir_detalle(agente_centro, widget_norte)


def test_lo_propio_si_se_ve(agente_centro, widget_centro):
    respuesta = _pedir_detalle(agente_centro, widget_centro)

    assert respuesta.status_code == 200


def test_cross_office_por_url_en_un_endpoint_real(client, gestor_centro, agente_norte):
    """Editar un usuario de otra oficina: 404, no 403."""
    client.force_login(gestor_centro)

    respuesta = client.get(reverse("accounts:user_update", args=[agente_norte.pk]))

    assert respuesta.status_code == 404


def test_desactivar_por_post_a_alguien_de_otra_oficina_da_404(client, gestor_centro, agente_norte):
    client.force_login(gestor_centro)

    respuesta = client.post(reverse("accounts:user_deactivate", args=[agente_norte.pk]))

    agente_norte.refresh_from_db()
    assert respuesta.status_code == 404
    assert agente_norte.is_active is True


# --- formulario -------------------------------------------------------------


def test_el_formulario_rechaza_una_oficina_ajena(gestor_centro, norte, centro):
    """El POST manipulado no pasa la validacion, no solo no se ve el checkbox."""
    from apps.accounts.forms import UserForm

    form = UserForm(
        data={
            "email": "nuevo@ejemplo.es",
            "first_name": "Nuevo",
            "last_name": "Usuario",
            "phone": "",
            "role": "",
            "offices": [norte.pk],
        },
        user=gestor_centro,
    )

    assert not form.is_valid()
    assert "offices" in form.errors


def test_el_formulario_acepta_la_oficina_propia(gestor_centro, centro):
    from apps.accounts.forms import UserForm

    form = UserForm(
        data={
            "email": "nuevo@ejemplo.es",
            "first_name": "Nuevo",
            "last_name": "Usuario",
            "phone": "",
            "role": "",
            "offices": [centro.pk],
        },
        user=gestor_centro,
    )

    assert form.is_valid(), form.errors


def test_crear_usuario_con_oficina_ajena_por_post_no_cuela(client, gestor_centro, norte):
    from apps.accounts.models import User

    client.force_login(gestor_centro)

    respuesta = client.post(
        reverse("accounts:user_create"),
        {
            "email": "colado@ejemplo.es",
            "first_name": "Colado",
            "last_name": "Por POST",
            "phone": "",
            "role": "",
            "offices": [norte.pk],
        },
    )

    assert respuesta.status_code == 200  # vuelve al formulario con el error
    assert not User.objects.filter(email="colado@ejemplo.es").exists()
