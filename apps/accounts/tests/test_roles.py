"""Roles del sistema y el comando que los mantiene."""

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command

from apps.accounts.models import Role
from apps.accounts.roles import ROLE_SPECS

pytestmark = pytest.mark.django_db


def test_sync_roles_crea_los_roles_declarados():
    call_command("sync_roles", verbosity=0)

    assert Role.objects.filter(is_system=True).count() == len(ROLE_SPECS)
    assert Role.objects.filter(code="administracion").exists()


def test_sync_roles_es_idempotente():
    call_command("sync_roles", verbosity=0)
    permisos_antes = Role.objects.get(code="administracion").permissions.count()

    call_command("sync_roles", verbosity=0)

    assert Role.objects.filter(is_system=True).count() == len(ROLE_SPECS)
    assert Role.objects.get(code="administracion").permissions.count() == permisos_antes


def test_sync_roles_reparte_los_permisos_custom_que_ya_existen():
    call_command("sync_roles", verbosity=0)

    administracion = Role.objects.get(code="administracion")
    codigos = {
        f"{app}.{codename}"
        for app, codename in administracion.permissions.values_list(
            "content_type__app_label", "codename"
        )
    }

    assert "accounts.manage_users" in codigos
    assert "pricing.manage_rates" in codigos
    assert "settings_app.access_settings" in codigos
    assert "reservations.cancel_reservation" in codigos
    assert "availability.override_availability" in codigos


def test_prune_quita_lo_que_ya_no_toca():
    call_command("sync_roles", verbosity=0)
    consulta = Role.objects.get(code="consulta")
    consulta.permissions.add(
        Permission.objects.get(content_type__app_label="accounts", codename="manage_users")
    )

    call_command("sync_roles", "--prune", verbosity=0)

    assert not consulta.permissions.filter(codename="manage_users").exists()


@pytest.mark.parametrize(
    "codigo",
    [
        "accounts.manage_users",
        "settings_app.access_settings",
        "pricing.manage_rates",
        "billing.view_billing",
        "billing.add_payment",
        "reservations.change_reservation_price",
        "reservations.cancel_reservation",
        "reservations.delete_reservation",
        "availability.override_availability",
    ],
)
def test_los_permisos_custom_existen(codigo):
    app_label, _, codename = codigo.partition(".")

    assert Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).exists(), f"falta el permiso {codigo}"
