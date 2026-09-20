"""CRUD de oficinas: listado, filtros, alta, edicion y baja logica."""

import pytest
from django.urls import NoReverseMatch, reverse

from apps.core.models import PhysicalDeleteNotAllowed
from apps.offices.models import Office

from .factories import OfficeFactory, OfficePoolFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def datos_oficina(**cambios):
    datos = {
        "code": "manacor",
        "name": "Manacor",
        "pool": "",
        "address": "Carrer Major 1",
        "city": "Manacor",
        "province": "Valencia",
        "postal_code": "07500",
        "country": "ES",
        "phone": "971000000",
        "email": "manacor@ejemplo.es",
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------


def test_el_listado_muestra_activas_y_desactivadas(client, gestor_maestros):
    OfficeFactory(code="viva", name="Oficina Viva")
    OfficeFactory(code="cerrada", name="Oficina Cerrada", is_active=False)
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("offices:office_list")).content.decode()

    assert "Oficina Viva" in contenido
    assert "Oficina Cerrada" in contenido


def test_la_busqueda_recorta_el_listado(client, gestor_maestros):
    OfficeFactory(code="inca", name="Oficina Inca", city="Inca")
    OfficeFactory(code="mahon", name="Oficina Mahon", city="Mahon")
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("offices:office_list"), {"q": "mahon"}).content.decode()

    assert "Oficina Mahon" in contenido
    assert "Oficina Inca" not in contenido


def test_el_filtro_de_estado_separa_las_desactivadas(client, gestor_maestros):
    OfficeFactory(code="viva", name="Oficina Viva")
    OfficeFactory(code="cerrada", name="Oficina Cerrada", is_active=False)
    client.force_login(gestor_maestros)
    url = reverse("offices:office_list")

    activas = client.get(url, {"estado": "activo"}).content.decode()
    inactivas = client.get(url, {"estado": "inactivo"}).content.decode()

    assert "Oficina Viva" in activas and "Oficina Cerrada" not in activas
    assert "Oficina Cerrada" in inactivas and "Oficina Viva" not in inactivas


def test_el_filtro_por_grupo(client, gestor_maestros):
    grupo = OfficePoolFactory(code="bahia", name="Bahia")
    OfficeFactory(code="dentro", name="Oficina Dentro", pool=grupo)
    OfficeFactory(code="fuera", name="Oficina Fuera")
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("offices:office_list"), {"pool": grupo.pk}).content.decode()

    assert "Oficina Dentro" in contenido
    assert "Oficina Fuera" not in contenido


def test_con_htmx_solo_viaja_la_tabla(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.get(reverse("offices:office_list"), headers=HTMX)
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "<html" not in contenido
    assert 'id="tabla-oficinas"' in contenido


# ---------------------------------------------------------------------------
# Alta y edicion
# ---------------------------------------------------------------------------


def test_el_alta_crea_la_oficina_y_avisa(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("offices:office_create"), datos_oficina(), headers=HTMX)

    assert respuesta.status_code == 200
    assert respuesta.content == b""  # cuerpo vacio: cierra el modal
    assert "crud:guardado" in respuesta.headers["HX-Trigger"]
    assert "Manacor" in respuesta.headers["HX-Trigger"]
    assert Office.objects.filter(code="manacor").exists()


def test_dos_oficinas_con_el_mismo_codigo_no_pasan(client, gestor_maestros):
    """Requisito: el codigo es unico y el error tiene que ser legible."""
    OfficeFactory(code="manacor", name="Manacor")
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("offices:office_create"),
        datos_oficina(name="Manacor Bis"),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "Ya existe una oficina con ese codigo." in respuesta.content.decode()
    assert Office.objects.filter(code="manacor").count() == 1


def test_el_codigo_se_guarda_en_minusculas(client, gestor_maestros):
    """ "PMI" y "pmi" no pueden ser dos oficinas distintas."""
    client.force_login(gestor_maestros)

    client.post(reverse("offices:office_create"), datos_oficina(code="MANACOR"), headers=HTMX)

    assert Office.objects.filter(code="manacor").exists()


def test_el_formulario_invalido_devuelve_422_con_los_errores(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("offices:office_create"),
        datos_oficina(postal_code="123"),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "cinco digitos" in respuesta.content.decode()
    assert not Office.objects.filter(code="manacor").exists()


def test_la_edicion_guarda_los_cambios(client, gestor_maestros):
    oficina = OfficeFactory(code="manacor", name="Manacor")
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("offices:office_update", args=[oficina.pk]),
        datos_oficina(name="Manacor Centro"),
        headers=HTMX,
    )
    oficina.refresh_from_db()

    assert respuesta.status_code == 200
    assert oficina.name == "Manacor Centro"


def test_el_alta_por_url_directa_sirve_la_pagina_entera(client, gestor_maestros):
    """Sin HTMX el formulario tiene que seguir siendo usable."""
    client.force_login(gestor_maestros)

    respuesta = client.get(reverse("offices:office_create"))

    assert respuesta.status_code == 200
    assert "<html" in respuesta.content.decode()


def test_sin_htmx_el_alta_redirige_al_listado(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("offices:office_create"), datos_oficina())

    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == reverse("offices:office_list")


# ---------------------------------------------------------------------------
# Baja logica
# ---------------------------------------------------------------------------


def test_desactivar_y_reactivar(client, gestor_maestros):
    oficina = OfficeFactory(code="manacor", name="Manacor")
    client.force_login(gestor_maestros)

    client.post(reverse("offices:office_deactivate", args=[oficina.pk]), headers=HTMX)
    oficina.refresh_from_db()
    assert oficina.is_active is False

    client.post(reverse("offices:office_activate", args=[oficina.pk]), headers=HTMX)
    oficina.refresh_from_db()
    assert oficina.is_active is True


def test_no_se_desactiva_una_oficina_con_usuarios_activos(client, gestor_maestros, centro):
    """El servicio se niega y el usuario lee por que, sin 500 de por medio."""
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("offices:office_deactivate", args=[centro.pk]), headers=HTMX)
    centro.refresh_from_db()

    assert respuesta.status_code == 200
    assert "usuarios activos" in respuesta.headers["HX-Trigger"]
    assert "crud:guardado" not in respuesta.headers["HX-Trigger"]
    assert centro.is_active is True


def test_una_oficina_desactivada_no_esta_en_el_alcance_del_usuario(client, gestor_maestros, centro):
    """Baja logica no es borrado: la fila sigue, pero deja de operar."""
    from apps.offices.selectors import offices_for_user

    centro.deactivate()

    assert Office.objects.filter(pk=centro.pk).exists()
    assert centro not in offices_for_user(gestor_maestros)


def test_borrar_de_verdad_esta_prohibido():
    """La opcion no existe en la interfaz, y desde codigo revienta."""
    oficina = OfficeFactory(code="manacor", name="Manacor")

    with pytest.raises(PhysicalDeleteNotAllowed):
        oficina.delete()
    with pytest.raises(PhysicalDeleteNotAllowed):
        Office.objects.filter(pk=oficina.pk).delete()
    assert Office.objects.filter(pk=oficina.pk).exists()


def test_no_hay_ruta_de_borrado():
    with pytest.raises(NoReverseMatch):
        reverse("offices:office_delete", args=[1])


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("offices:office_list", "get", False),
    ("offices:office_create", "get", False),
    ("offices:office_create", "post", False),
    ("offices:office_update", "get", True),
    ("offices:office_update", "post", True),
    ("offices:office_activate", "post", True),
    ("offices:office_deactivate", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, vista, metodo, con_objeto):
    oficina = OfficeFactory(code="manacor", name="Manacor")
    client.force_login(agente_centro)

    url = reverse(vista, args=[oficina.pk] if con_objeto else [])
    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code == 403


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_con_permiso_no_da_403(client, gestor_maestros, vista, metodo, con_objeto):
    oficina = OfficeFactory(code="manacor", name="Manacor")
    client.force_login(gestor_maestros)

    url = reverse(vista, args=[oficina.pk] if con_objeto else [])
    respuesta = getattr(client, metodo)(url, {})

    assert respuesta.status_code != 403


def test_el_boton_de_alta_no_se_ensena_sin_permiso(client, centro, rol_mostrador):
    """Esconderlo no es la validacion, pero tampoco se ensenan puertas cerradas."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    solo_lectura = UserFactory(
        email="lectura@ejemplo.es",
        role=RoleFactory(code="solo-ver", permissions=["offices.view_office"]),
        offices=[centro],
    )
    client.force_login(solo_lectura)

    contenido = client.get(reverse("offices:office_list")).content.decode()

    assert "Nueva oficina" not in contenido
