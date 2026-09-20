"""Extras: forma de cobro, tope por dia y baja logica."""

from decimal import Decimal

import pytest
from django.urls import NoReverseMatch, reverse

from apps.core.models import PhysicalDeleteNotAllowed
from apps.pricing.models import CalculationType, Extra

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def datos_extra(**cambios):
    datos = {
        "code": "silla",
        "name": "Silla de bebe",
        "description": "",
        "calculation_type": CalculationType.PER_DAY,
        "price": "5.00",
        "tax_rate": "21.00",
        "max_quantity": 2,
        "max_amount": "50.00",
        "requires_driver_data": "",
        "sort_order": 10,
    }
    datos.update(cambios)
    return datos


def crear_extra(**cambios):
    valores = {
        "code": "silla",
        "name": "Silla de bebe",
        "calculation_type": CalculationType.PER_DAY,
        "price": Decimal("5.00"),
        "max_quantity": 2,
    }
    valores.update(cambios)
    return Extra.objects.create(**valores)


def test_el_alta_crea_el_extra_con_su_tope(client, gestor_maestros):
    """El caso tipico: 5 EUR/dia con maximo 50 EUR."""
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("pricing:extra_create"), datos_extra(), headers=HTMX)
    extra = Extra.objects.get(code="silla")

    assert respuesta.status_code == 200
    assert extra.price == Decimal("5.00")
    assert extra.max_amount == Decimal("50.00")
    assert extra.has_cap


def test_los_importes_son_decimales_exactos(client, gestor_maestros):
    """Nada de float: el dinero se guarda como Decimal con dos cifras."""
    client.force_login(gestor_maestros)

    client.post(reverse("pricing:extra_create"), datos_extra(price="12.35"), headers=HTMX)

    assert Extra.objects.get(code="silla").price == Decimal("12.35")


def test_el_tope_solo_vale_para_los_extras_por_dia(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("pricing:extra_create"),
        datos_extra(calculation_type=CalculationType.ONCE, max_amount="50.00"),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "solo se aplica a los extras que se cobran por dia" in respuesta.content.decode()


def test_el_tope_no_puede_ser_menor_que_un_dia(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("pricing:extra_create"),
        datos_extra(price="10.00", max_amount="5.00"),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "no puede ser menor" in respuesta.content.decode()


def test_un_extra_sin_tope_es_valido(client, gestor_maestros):
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("pricing:extra_create"), datos_extra(max_amount=""), headers=HTMX
    )

    assert respuesta.status_code == 200
    assert Extra.objects.get(code="silla").max_amount is None


def test_dos_extras_con_el_mismo_codigo_no_pasan(client, gestor_maestros):
    crear_extra()
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("pricing:extra_create"), datos_extra(name="Silla bis"), headers=HTMX
    )

    assert respuesta.status_code == 422
    assert "Ya existe un extra con ese codigo." in respuesta.content.decode()


def test_el_segundo_conductor_pide_datos_de_conductor(client, gestor_maestros):
    client.force_login(gestor_maestros)

    client.post(
        reverse("pricing:extra_create"),
        datos_extra(
            code="conductor2",
            name="Segundo conductor",
            calculation_type=CalculationType.PER_RESERVATION,
            max_amount="",
            requires_driver_data="on",
        ),
        headers=HTMX,
    )

    assert Extra.objects.get(code="conductor2").requires_driver_data is True


def test_retirar_y_reponer(client, gestor_maestros):
    extra = crear_extra()
    client.force_login(gestor_maestros)

    client.post(reverse("pricing:extra_deactivate", args=[extra.pk]), headers=HTMX)
    extra.refresh_from_db()
    assert extra.is_active is False

    client.post(reverse("pricing:extra_activate", args=[extra.pk]), headers=HTMX)
    extra.refresh_from_db()
    assert extra.is_active is True


def test_un_extra_retirado_sigue_en_el_listado(client, gestor_maestros):
    crear_extra(is_active=False, name="Extra Retirado")
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("pricing:extra_list"), {"estado": "inactivo"}).content.decode()

    assert "Extra Retirado" in contenido


def test_un_extra_no_se_borra():
    extra = crear_extra()

    with pytest.raises(PhysicalDeleteNotAllowed):
        extra.delete()


def test_no_hay_ruta_de_borrado():
    with pytest.raises(NoReverseMatch):
        reverse("pricing:extra_delete", args=[1])


ENDPOINTS = [
    ("pricing:extra_list", "get", False),
    ("pricing:extra_create", "get", False),
    ("pricing:extra_create", "post", False),
    ("pricing:extra_update", "get", True),
    ("pricing:extra_activate", "post", True),
    ("pricing:extra_deactivate", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, vista, metodo, con_objeto):
    extra = crear_extra()
    client.force_login(agente_centro)

    url = reverse(vista, args=[extra.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403
