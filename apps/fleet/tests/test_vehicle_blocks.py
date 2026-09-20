"""Bloqueos de vehiculo: el solape lo impide la base de datos, no el formulario."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.fleet.models import VehicleBlock
from apps.fleet.services import FleetServiceError, save_block

from .factories import VehicleBlockFactory, VehicleFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


def iso(momento):
    """Formato que manda el input datetime-local del navegador."""
    return timezone.localtime(momento).strftime("%Y-%m-%dT%H:%M")


def test_dos_bloqueos_del_mismo_vehiculo_no_pueden_solaparse(centro):
    """Test obligatorio: lo rechaza la constraint de exclusion, con IntegrityError."""
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)
    VehicleBlockFactory(vehicle=vehiculo, start_at=inicio, end_at=inicio + timedelta(days=3))

    with pytest.raises(IntegrityError) as error, transaction.atomic():
        # Se escribe a pelo, saltandose el formulario y el servicio: lo que se
        # comprueba es que la base de datos aguanta el golpe por si sola.
        VehicleBlock.objects.create(
            vehicle=vehiculo,
            start_at=inicio + timedelta(days=1),
            end_at=inicio + timedelta(days=4),
        )

    assert "fleet_block_sin_solapes" in str(error.value)


def test_dos_bloqueos_pegados_si_valen(centro):
    """El rango es [inicio, fin): que uno acabe cuando empieza el otro no es solape."""
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)
    primero = VehicleBlockFactory(
        vehicle=vehiculo, start_at=inicio, end_at=inicio + timedelta(days=2)
    )

    segundo = VehicleBlock.objects.create(
        vehicle=vehiculo, start_at=primero.end_at, end_at=primero.end_at + timedelta(days=1)
    )

    assert VehicleBlock.objects.filter(vehicle=vehiculo).count() == 2
    assert segundo.pk


def test_dos_coches_distintos_si_pueden_estar_bloqueados_a_la_vez(centro):
    inicio = timezone.now() + timedelta(days=1)
    uno = VehicleFactory(plate="1111AAA", current_office=centro)
    otro = VehicleFactory(plate="2222BBB", current_office=centro)

    VehicleBlockFactory(vehicle=uno, start_at=inicio, end_at=inicio + timedelta(days=2))
    VehicleBlockFactory(vehicle=otro, start_at=inicio, end_at=inicio + timedelta(days=2))

    assert VehicleBlock.objects.count() == 2


def test_el_fin_tiene_que_ser_posterior_al_inicio(centro):
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)

    with pytest.raises(IntegrityError) as error, transaction.atomic():
        VehicleBlock.objects.create(
            vehicle=vehiculo, start_at=inicio, end_at=inicio - timedelta(hours=1)
        )

    assert "fin_posterior_al_inicio" in str(error.value)


def test_el_servicio_traduce_el_choque_a_un_aviso(centro):
    """Entre la comprobacion del formulario y el INSERT cabe otro usuario."""
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)
    VehicleBlockFactory(vehicle=vehiculo, start_at=inicio, end_at=inicio + timedelta(days=3))

    solapado = VehicleBlock(
        vehicle=vehiculo, start_at=inicio + timedelta(days=1), end_at=inicio + timedelta(days=4)
    )

    with pytest.raises(FleetServiceError, match="se solapa"):
        save_block(block=solapado)


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------


def test_el_alta_crea_el_bloqueo(client, gestor_maestros, centro):
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:block_create"),
        {
            "vehicle": vehiculo.pk,
            "start_at": iso(inicio),
            "end_at": iso(inicio + timedelta(days=2)),
            "reason": "workshop",
            "notes": "Cambio de embrague",
        },
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert vehiculo.blocks.count() == 1


def test_el_formulario_avisa_del_solape_antes_de_llegar_a_la_base_de_datos(
    client, gestor_maestros, centro
):
    vehiculo = VehicleFactory(current_office=centro)
    inicio = timezone.now() + timedelta(days=1)
    VehicleBlockFactory(vehicle=vehiculo, start_at=inicio, end_at=inicio + timedelta(days=3))
    client.force_login(gestor_maestros)

    respuesta = client.post(
        reverse("fleet:block_create"),
        {
            "vehicle": vehiculo.pk,
            "start_at": iso(inicio + timedelta(days=1)),
            "end_at": iso(inicio + timedelta(days=4)),
            "reason": "cleaning",
            "notes": "",
        },
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "ya esta bloqueado" in respuesta.content.decode()
    assert vehiculo.blocks.count() == 1


def test_solo_se_bloquean_coches_de_tus_oficinas(gestor_maestros, norte):
    from apps.fleet.forms import VehicleBlockForm

    ajeno = VehicleFactory(plate="2222BBB", current_office=norte)

    formulario = VehicleBlockForm(user=gestor_maestros)

    assert ajeno not in formulario.fields["vehicle"].queryset


def test_anular_un_bloqueo_lo_borra_de_verdad(client, gestor_maestros, centro):
    """Un bloqueo marcado como anulado seguiria ocupando hueco en la constraint."""
    vehiculo = VehicleFactory(current_office=centro)
    bloqueo = VehicleBlockFactory(vehicle=vehiculo)
    client.force_login(gestor_maestros)

    respuesta = client.post(reverse("fleet:block_delete", args=[bloqueo.pk]), headers=HTMX)

    assert respuesta.status_code == 200
    assert not VehicleBlock.objects.filter(pk=bloqueo.pk).exists()


def test_tras_anular_el_hueco_vuelve_a_estar_libre(client, gestor_maestros, centro):
    vehiculo = VehicleFactory(current_office=centro)
    bloqueo = VehicleBlockFactory(vehicle=vehiculo)
    client.force_login(gestor_maestros)

    client.post(reverse("fleet:block_delete", args=[bloqueo.pk]), headers=HTMX)
    nuevo = VehicleBlock.objects.create(
        vehicle=vehiculo, start_at=bloqueo.start_at, end_at=bloqueo.end_at
    )

    assert nuevo.pk


def test_el_listado_solo_ensena_bloqueos_de_tu_flota(client, gestor_maestros, centro, norte):
    VehicleBlockFactory(vehicle=VehicleFactory(plate="1111AAA", current_office=centro))
    VehicleBlockFactory(vehicle=VehicleFactory(plate="2222BBB", current_office=norte))
    client.force_login(gestor_maestros)

    contenido = client.get(reverse("fleet:block_list")).content.decode()

    assert "1111AAA" in contenido
    assert "2222BBB" not in contenido


ENDPOINTS = [
    ("fleet:block_list", "get", False),
    ("fleet:block_create", "get", False),
    ("fleet:block_create", "post", False),
    ("fleet:block_update", "get", True),
    ("fleet:block_delete", "post", True),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, centro, vista, metodo, con_objeto):
    bloqueo = VehicleBlockFactory(vehicle=VehicleFactory(current_office=centro))
    client.force_login(agente_centro)

    url = reverse(vista, args=[bloqueo.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403
