"""CRUD de categorias de vehiculo y su regla de oro: retirar no es borrar."""

import pytest
from django.urls import NoReverseMatch, reverse

from apps.core.models import PhysicalDeleteNotAllowed
from apps.fleet.models import Fuel, Transmission, VehicleCategory

from .factories import VehicleCategoryFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def datos_categoria(**cambios):
    datos = {
        "code": "suv",
        "name": "SUV",
        "description": "",
        "seats": 5,
        "doors": 5,
        "luggage": 3,
        "transmission": Transmission.AUTOMATIC,
        "fuel": Fuel.HYBRID,
        "air_conditioning": "on",
        "sort_order": 40,
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Listado y filtros
# ---------------------------------------------------------------------------


def test_el_listado_muestra_las_retiradas(client, gestor_maestros):
    VehicleCategoryFactory(code="eco", name="Economico")
    VehicleCategoryFactory(code="antigua", name="Categoria Antigua", is_active=False)
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("fleet:category_list")).content.decode()

    assert "Economico" in contenido
    assert "Categoria Antigua" in contenido


def test_el_filtro_de_estado_encuentra_la_retirada(client, gestor_maestros):
    """Requisito: la categoria inactiva sigue estando en el listado historico."""
    VehicleCategoryFactory(code="eco", name="Economico")
    VehicleCategoryFactory(code="antigua", name="Categoria Antigua", is_active=False)
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("fleet:category_list"), {"estado": "inactivo"}).content.decode()

    assert "Categoria Antigua" in contenido
    assert "Economico" not in contenido


def test_los_filtros_de_cambio_y_combustible(client, gestor_maestros):
    VehicleCategoryFactory(code="manual", name="Manual Gasolina")
    VehicleCategoryFactory(
        code="auto", name="Auto Electrico", transmission=Transmission.AUTOMATIC, fuel=Fuel.ELECTRIC
    )
    client.force_login(gestor_maestros)
    url = reverse("fleet:category_list")

    automaticas = client.get(url, {"transmission": Transmission.AUTOMATIC}).content.decode()
    electricas = client.get(url, {"fuel": Fuel.ELECTRIC}).content.decode()

    assert "Auto Electrico" in automaticas and "Manual Gasolina" not in automaticas
    assert "Auto Electrico" in electricas and "Manual Gasolina" not in electricas


# ---------------------------------------------------------------------------
# Alta, edicion y codigos
# ---------------------------------------------------------------------------


def test_el_alta_crea_la_categoria(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("fleet:category_create"), datos_categoria(), headers=HTMX)
    categoria = VehicleCategory.objects.get(code="suv")

    assert respuesta.status_code == 200
    assert "crud:guardado" in respuesta.headers["HX-Trigger"]
    assert categoria.transmission == Transmission.AUTOMATIC
    assert categoria.air_conditioning is True


def test_dos_categorias_con_el_mismo_codigo_no_pasan(client, gestor_maestros):
    VehicleCategoryFactory(code="suv", name="SUV")
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:category_create"), datos_categoria(name="SUV Bis"), headers=HTMX
    )

    assert respuesta.status_code == 422
    assert "Ya existe una categoria con ese codigo." in respuesta.content.decode()
    assert VehicleCategory.objects.filter(code="suv").count() == 1


def test_la_edicion_guarda_los_cambios(client, gestor_maestros):
    categoria = VehicleCategoryFactory(code="suv", name="SUV")
    client.force_login(gestor_maestros)

    client.post(
        reverse("fleet:category_update", args=[categoria.pk]),
        datos_categoria(name="SUV Premium"),
        headers=HTMX,
    )
    categoria.refresh_from_db()

    assert categoria.name == "SUV Premium"


# ---------------------------------------------------------------------------
# Retirar del catalogo
# ---------------------------------------------------------------------------


def test_una_categoria_retirada_no_se_ofrece_en_una_reserva_nueva(client, gestor_maestros):
    """Criterio de aceptacion.

    La categoria retirada desaparece del selector con el que se vende, pero
    sigue existiendo y sigue saliendo en el listado, que es desde donde se
    repone. El selector es `fleet.forms.VehicleCategoryChoiceField`, el campo
    que usaran los formularios de reserva.
    """
    from apps.fleet.forms import VehicleCategoryChoiceField

    vigente = VehicleCategoryFactory(code="eco", name="Economico")
    retirada = VehicleCategoryFactory(code="antigua", name="Categoria Antigua")
    client.force_login(gestor_maestros)

    client.post(reverse("fleet:category_deactivate", args=[retirada.pk]), headers=HTMX)
    retirada.refresh_from_db()
    ofrecidas = list(VehicleCategoryChoiceField().queryset)

    assert retirada.is_active is False
    assert vigente in ofrecidas
    assert retirada not in ofrecidas

    # ...y en el listado historico sigue estando.
    listado = client.get(reverse("fleet:category_list"), {"estado": "inactivo"})
    assert "Categoria Antigua" in listado.content.decode()


def test_el_selector_de_venta_rechaza_una_categoria_retirada():
    """Manipular el POST tampoco cuela: el queryset del campo es quien valida."""
    from django.core.exceptions import ValidationError

    from apps.fleet.forms import VehicleCategoryChoiceField

    retirada = VehicleCategoryFactory(code="antigua", name="Categoria Antigua", is_active=False)

    with pytest.raises(ValidationError):
        VehicleCategoryChoiceField().clean(str(retirada.pk))


def test_retirar_no_toca_lo_ya_vendido():
    """Una categoria retirada se sigue leyendo igual: el historico la necesita."""
    from apps.fleet.services import set_category_active

    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    vendida_como = categoria.pk

    set_category_active(category=categoria, active=False)
    historica = VehicleCategory.objects.get(pk=vendida_como)

    assert historica.name == "Economico"
    assert historica.is_active is False


def test_reponer_la_devuelve_al_catalogo(client, gestor_maestros):
    from apps.fleet.selectors import selectable_categories

    categoria = VehicleCategoryFactory(code="eco", name="Economico", is_active=False)
    client.force_login(gestor_maestros)

    client.post(reverse("fleet:category_activate", args=[categoria.pk]), headers=HTMX)

    assert categoria in selectable_categories()


def test_borrar_de_verdad_esta_prohibido():
    categoria = VehicleCategoryFactory(code="eco", name="Economico")

    with pytest.raises(PhysicalDeleteNotAllowed):
        categoria.delete()
    with pytest.raises(PhysicalDeleteNotAllowed):
        VehicleCategory.objects.filter(pk=categoria.pk).delete()
    assert VehicleCategory.objects.filter(pk=categoria.pk).exists()


def test_no_hay_ruta_de_borrado():
    with pytest.raises(NoReverseMatch):
        reverse("fleet:category_delete", args=[1])


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("fleet:category_list", "get", False),
    ("fleet:category_create", "get", False),
    ("fleet:category_create", "post", False),
    ("fleet:category_update", "get", True),
    ("fleet:category_update", "post", True),
    ("fleet:category_activate", "post", True),
    ("fleet:category_deactivate", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, vista, metodo, con_objeto):
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    client.force_login(agente_centro)

    url = reverse(vista, args=[categoria.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_con_permiso_no_da_403(client, gestor_maestros, vista, metodo, con_objeto):
    categoria = VehicleCategoryFactory(code="eco", name="Economico")
    client.force_login(gestor_maestros)

    url = reverse(vista, args=[categoria.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code != 403
