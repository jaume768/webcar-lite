"""Mixins de core, probados sobre un modelo concreto de test."""

import pytest

from apps.core.middleware import current_user
from apps.core.tests.testapp.models import Widget

pytestmark = pytest.mark.django_db


def test_timestamped_rellena_las_marcas():
    widget = Widget.objects.create(name="uno")

    assert widget.created_at is not None
    assert widget.updated_at is not None

    anterior = widget.updated_at
    widget.name = "uno bis"
    widget.save()
    widget.refresh_from_db()

    assert widget.updated_at > anterior
    assert widget.created_at < widget.updated_at


def test_activable_por_defecto_activo_y_filtrable():
    activo = Widget.objects.create(name="activo")
    baja = Widget.objects.create(name="baja")
    baja.deactivate()

    assert activo.is_active is True
    assert list(Widget.objects.active()) == [activo]
    assert list(Widget.objects.inactive()) == [baja]
    # El manager no filtra por su cuenta: hay que pedirlo explicitamente.
    assert Widget.objects.count() == 2


def test_activable_no_borra_reactiva():
    widget = Widget.objects.create(name="furgoneta")
    widget.deactivate()
    widget.refresh_from_db()
    assert widget.is_active is False

    widget.activate()
    widget.refresh_from_db()
    assert widget.is_active is True


def test_userstamped_toma_el_usuario_del_contexto(django_user_model):
    ana = django_user_model.objects.create_user(username="ana")
    luis = django_user_model.objects.create_user(username="luis")

    with current_user(ana):
        widget = Widget.objects.create(name="coche")

    assert widget.created_by == ana
    assert widget.updated_by == ana

    with current_user(luis):
        widget.name = "coche 2"
        widget.save()

    widget.refresh_from_db()
    assert widget.created_by == ana, "created_by no se reescribe nunca"
    assert widget.updated_by == luis


def test_userstamped_sin_usuario_en_contexto():
    """Comandos, tareas y migraciones guardan sin usuario, no revientan."""
    widget = Widget.objects.create(name="sin usuario")

    assert widget.created_by is None
    assert widget.updated_by is None


def test_userstamped_respeta_update_fields(django_user_model):
    """Un save parcial tambien tiene que dejar constancia de quien lo hizo."""
    ana = django_user_model.objects.create_user(username="ana")
    luis = django_user_model.objects.create_user(username="luis")

    with current_user(ana):
        widget = Widget.objects.create(name="coche")

    with current_user(luis):
        widget.name = "coche 2"
        widget.save(update_fields=["name"])

    widget.refresh_from_db()
    assert widget.name == "coche 2"
    assert widget.updated_by == luis
