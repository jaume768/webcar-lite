"""Aplica sobre la base de datos los roles declarados en `roles.py`."""

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role
from apps.accounts.roles import ROLE_SPECS


def _resolver(codigos: list[str]) -> tuple[list[Permission], list[str]]:
    """Traduce `app_label.codename` a permisos. Devuelve tambien los que faltan."""
    encontrados, pendientes = [], []
    for codigo in codigos:
        app_label, _, codename = codigo.partition(".")
        permiso = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if permiso is None:
            pendientes.append(codigo)
        else:
            encontrados.append(permiso)
    return encontrados, pendientes


class Command(BaseCommand):
    help = "Crea o actualiza los roles del sistema y su conjunto de permisos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Quita del rol los permisos que ya no estan en la definicion.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        pendientes_totales = set()

        for spec in ROLE_SPECS:
            role, creado = Role.objects.update_or_create(
                code=spec.code,
                defaults={
                    "name": spec.name,
                    "description": spec.description,
                    "is_system": True,
                },
            )
            permisos, pendientes = _resolver(spec.permissions)
            pendientes_totales.update(pendientes)

            if options["prune"]:
                role.permissions.set(permisos)
            else:
                role.permissions.add(*permisos)

            verbo = "creado" if creado else "actualizado"
            self.stdout.write(f"Rol {role.code}: {verbo}, {len(permisos)} permisos")

        if pendientes_totales:
            self.stdout.write(
                self.style.WARNING(
                    "Permisos aun inexistentes (llegaran con sus apps): "
                    + ", ".join(sorted(pendientes_totales))
                )
            )
            self.stdout.write("Vuelve a ejecutar sync_roles cuando esas apps tengan modelos.")
