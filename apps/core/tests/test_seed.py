import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_seed_crea_el_superusuario(settings):
    settings.DEBUG = True

    call_command("seed", username="mostrador", email="mostrador@localhost", password="secreto")

    usuario = get_user_model().objects.get(username="mostrador")
    assert usuario.is_superuser and usuario.is_staff
    assert usuario.check_password("secreto")


@pytest.mark.django_db
def test_seed_es_idempotente(settings):
    settings.DEBUG = True

    call_command("seed", username="mostrador")
    call_command("seed", username="mostrador")

    assert get_user_model().objects.filter(username="mostrador").count() == 1


@pytest.mark.django_db
def test_seed_se_niega_a_correr_sin_debug(settings):
    settings.DEBUG = False

    with pytest.raises(CommandError):
        call_command("seed")

    assert not get_user_model().objects.filter(username="admin").exists()
