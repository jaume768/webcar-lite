"""Datos minimos para arrancar un entorno de desarrollo."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Crea los datos iniciales de desarrollo. Es idempotente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.environ.get("SEED_ADMIN_USERNAME", "admin"),
            help="Usuario administrador a crear (por defecto: admin).",
        )
        parser.add_argument(
            "--email",
            default=os.environ.get("SEED_ADMIN_EMAIL", "admin@localhost"),
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("SEED_ADMIN_PASSWORD", "admin"),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from django.conf import settings

        if not settings.DEBUG:
            raise CommandError("seed solo se ejecuta con DEBUG=True. En produccion no.")

        user_model = get_user_model()
        username = options["username"]

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": options["email"],
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            user.set_password(options["password"])
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"Superusuario creado: {username}"))
        else:
            self.stdout.write(f"El superusuario {username} ya existe, no se toca.")

        self.stdout.write(
            "Sin datos de negocio todavia: flota, tarifas y oficinas llegan con sus apps."
        )
