"""Grupos de oficinas: la agrupacion con la que se resolvera el one-way."""

import pytest
from django.urls import reverse

from apps.offices.models import Office, OfficePool

from .factories import OfficeFactory, OfficePoolFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def test_el_alta_crea_el_grupo(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("offices:pool_create"),
        {"code": "BAHIA", "name": "Bahia de Palma", "description": ""},
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert OfficePool.objects.filter(code="bahia").exists()


def test_dos_grupos_con_el_mismo_codigo_no_pasan(client, gestor_maestros):
    OfficePoolFactory(code="bahia", name="Bahia")
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("offices:pool_create"),
        {"code": "bahia", "name": "Bahia Bis", "description": ""},
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "Ya existe un grupo con ese codigo." in respuesta.content.decode()


def test_una_oficina_pertenece_a_un_grupo(client, gestor_maestros):
    grupo = OfficePoolFactory(code="bahia", name="Bahia")
    client.force_login(gestor_maestros)

    client.post(
        reverse("offices:office_create"),
        {
            "code": "pmi",
            "name": "Aeropuerto",
            "pool": grupo.pk,
            "address": "",
            "city": "Palma",
            "province": "Illes Balears",
            "postal_code": "07611",
            "country": "ES",
            "phone": "",
            "email": "",
        },
        headers=HTMX,
    )

    assert Office.objects.get(code="pmi").pool == grupo
    assert list(grupo.offices.all()) == [Office.objects.get(code="pmi")]


def test_un_grupo_con_oficinas_activas_no_se_desactiva(client, gestor_maestros):
    """Bajarlo dejaria la disponibilidad calculando sobre algo que ya no se ve."""
    grupo = OfficePoolFactory(code="bahia", name="Bahia")
    OfficeFactory(code="pmi", name="Aeropuerto", pool=grupo)
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("offices:pool_deactivate", args=[grupo.pk]), headers=HTMX)
    grupo.refresh_from_db()

    assert respuesta.status_code == 200
    assert "oficinas activas" in respuesta.headers["HX-Trigger"]
    assert grupo.is_active is True


def test_un_grupo_vacio_si_se_desactiva(client, gestor_maestros):
    grupo = OfficePoolFactory(code="bahia", name="Bahia")
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("offices:pool_deactivate", args=[grupo.pk]), headers=HTMX)
    grupo.refresh_from_db()

    assert "crud:guardado" in respuesta.headers["HX-Trigger"]
    assert grupo.is_active is False


def test_un_grupo_desactivado_no_se_ofrece_al_editar_una_oficina(client, gestor_maestros):
    from apps.offices.forms import OfficeForm

    activo = OfficePoolFactory(code="bahia", name="Bahia")
    retirado = OfficePoolFactory(code="norte", name="Norte", is_active=False)

    disponibles = list(OfficeForm().fields["pool"].queryset)

    assert activo in disponibles
    assert retirado not in disponibles


def test_una_oficina_conserva_su_grupo_aunque_este_desactivado():
    """Editar el telefono no puede cambiarle el grupo por detras."""
    from apps.offices.forms import OfficeForm

    retirado = OfficePoolFactory(code="norte", name="Norte", is_active=False)
    oficina = OfficeFactory(code="alcudia", name="Alcudia", pool=retirado)

    disponibles = list(OfficeForm(instance=oficina).fields["pool"].queryset)

    assert retirado in disponibles
