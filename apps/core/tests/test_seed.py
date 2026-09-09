import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.offices.models import Office

pytestmark = pytest.mark.django_db


def test_seed_crea_el_superusuario(settings):
    settings.DEBUG = True

    call_command("seed", email="mostrador@ejemplo.es", password="secreto-largo-123")

    usuario = get_user_model().objects.get(email="mostrador@ejemplo.es")
    assert usuario.is_superuser and usuario.is_staff
    assert usuario.check_password("secreto-largo-123")


def test_seed_crea_las_oficinas_y_los_roles(settings):
    settings.DEBUG = True

    call_command("seed")

    from apps.accounts.models import Role

    assert Office.objects.count() == 3
    assert Role.objects.filter(is_system=True).count() == 4


def test_seed_es_idempotente(settings):
    settings.DEBUG = True

    call_command("seed", email="mostrador@ejemplo.es")
    call_command("seed", email="mostrador@ejemplo.es")

    assert get_user_model().objects.filter(email="mostrador@ejemplo.es").count() == 1
    assert Office.objects.count() == 3


def test_seed_se_niega_a_correr_sin_debug(settings):
    settings.DEBUG = False

    with pytest.raises(CommandError):
        call_command("seed")

    assert not get_user_model().objects.filter(email="admin@localhost").exists()
