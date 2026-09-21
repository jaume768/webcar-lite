"""Politicas de la empresa: solo administracion las gestiona."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import RoleFactory, UserFactory
from apps.settings_app.models import Policy

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_politicas(db, centro):
    return UserFactory(
        email="politicas@ejemplo.es",
        role=RoleFactory(
            code="politicas",
            name="Politicas",
            permissions=[
                "settings_app.view_policy",
                "settings_app.add_policy",
                "settings_app.change_policy",
            ],
        ),
        offices=[centro],
    )


def test_crear_una_politica_desde_el_modal(client, admin_politicas):
    client.force_login(admin_politicas)

    respuesta = client.post(
        reverse("settings_app:policy_create"),
        {"title": " Fianza ", "body": "Se devuelve.", "show_on_invoice": "on", "sort_order": 1},
        headers={"hx-request": "true"},
    )

    assert respuesta.status_code == 200
    assert Policy.objects.get().title == "Fianza"


def test_desactivar_no_borra(client, admin_politicas):
    politica = Policy.objects.create(title="Combustible", body="Lleno a lleno.")
    client.force_login(admin_politicas)

    client.post(reverse("settings_app:policy_deactivate", args=[politica.pk]))
    politica.refresh_from_db()

    assert not politica.is_active


def test_el_mostrador_no_ve_las_politicas(client, agente_centro):
    client.force_login(agente_centro)

    assert client.get(reverse("settings_app:policy_list")).status_code == 403
    assert client.get(reverse("settings_app:policy_create")).status_code == 403
