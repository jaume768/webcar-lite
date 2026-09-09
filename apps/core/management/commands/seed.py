"""Datos minimos para arrancar un entorno de desarrollo."""

import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Crea los datos iniciales de desarrollo. Es idempotente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default=os.environ.get("SEED_ADMIN_EMAIL", "admin@localhost"),
            help="Correo del superusuario (por defecto: admin@localhost).",
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

        from apps.offices.models import Office

        # Los roles del sistema son parte del arranque, no un dato de ejemplo.
        call_command("sync_roles")

        oficinas = [
            ("palma", "Palma Centro", "Palma", "Illes Balears", "07001"),
            ("alcudia", "Alcudia Puerto", "Alcudia", "Illes Balears", "07400"),
            ("pmi", "Aeropuerto PMI", "Palma", "Illes Balears", "07611"),
        ]
        for code, name, city, province, cp in oficinas:
            _, creada = Office.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "city": city,
                    "province": province,
                    "postal_code": cp,
                    "country": "ES",
                },
            )
            if creada:
                self.stdout.write(f"Oficina creada: {name}")

        user_model = get_user_model()
        email = options["email"].lower()
        user, creado = user_model.objects.get_or_create(
            email=email,
            defaults={"is_staff": True, "is_superuser": True, "first_name": "Admin"},
        )
        if creado:
            user.set_password(options["password"])
            user.save(update_fields=["password"])
            user.offices.set(Office.objects.all())
            self.stdout.write(self.style.SUCCESS(f"Superusuario creado: {email}"))
        else:
            self.stdout.write(f"El superusuario {email} ya existe, no se toca.")

        self.stdout.write(
            "Sin datos de negocio todavia: flota, tarifas y reservas llegan con sus apps."
        )
