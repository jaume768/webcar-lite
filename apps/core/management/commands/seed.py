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

        from apps.fleet.models import Fuel, Transmission, VehicleCategory
        from apps.offices.models import Office, OfficePool

        # Los roles del sistema son parte del arranque, no un dato de ejemplo.
        call_command("sync_roles")

        # Palma y el aeropuerto comparten flota: un one-way entre ellas no
        # descuenta capacidad. Alcudia responde de la suya.
        bahia, _ = OfficePool.objects.get_or_create(
            code="bahia-palma",
            defaults={
                "name": "Bahia de Palma",
                "description": "Palma centro y aeropuerto: la flota se mueve entre las dos.",
            },
        )

        oficinas = [
            ("palma", "Palma Centro", "Palma", "Illes Balears", "07001", bahia),
            ("alcudia", "Alcudia Puerto", "Alcudia", "Illes Balears", "07400", None),
            ("pmi", "Aeropuerto PMI", "Palma", "Illes Balears", "07611", bahia),
        ]
        for code, name, city, province, cp, pool in oficinas:
            _, creada = Office.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "city": city,
                    "province": province,
                    "postal_code": cp,
                    "country": "ES",
                    "pool": pool,
                },
            )
            if creada:
                self.stdout.write(f"Oficina creada: {name}")

        categorias = [
            ("eco", "Economico", 4, 3, 1, Transmission.MANUAL, Fuel.PETROL, 10),
            ("compacto", "Compacto", 5, 5, 2, Transmission.MANUAL, Fuel.DIESEL, 20),
            (
                "compacto-aut",
                "Compacto automatico",
                5,
                5,
                2,
                Transmission.AUTOMATIC,
                Fuel.PETROL,
                30,
            ),
            ("suv", "SUV", 5, 5, 3, Transmission.AUTOMATIC, Fuel.HYBRID, 40),
            ("furgon", "Furgoneta 7 plazas", 7, 5, 4, Transmission.MANUAL, Fuel.DIESEL, 50),
        ]
        for code, name, plazas, puertas, maletas, cambio, combustible, orden in categorias:
            _, creada = VehicleCategory.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "seats": plazas,
                    "doors": puertas,
                    "luggage": maletas,
                    "transmission": cambio,
                    "fuel": combustible,
                    "sort_order": orden,
                },
            )
            if creada:
                self.stdout.write(f"Categoria creada: {name}")

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

        self._crear_vehiculos()
        self._crear_extras()
        self._crear_tarifas()
        self._crear_clientes()

        self.stdout.write("Faltan las reservas: llegan con su app.")

    def _crear_vehiculos(self):
        from apps.fleet.models import Fuel, Transmission, Vehicle, VehicleCategory
        from apps.offices.models import Office

        palma = Office.objects.get(code="palma")
        pmi = Office.objects.get(code="pmi")
        flota = [
            ("1234ABC", "Seat", "Ibiza", "eco", palma, Fuel.PETROL, Transmission.MANUAL),
            ("2345BCD", "Renault", "Clio", "eco", palma, Fuel.PETROL, Transmission.MANUAL),
            ("3456CDE", "Seat", "Leon", "compacto", pmi, Fuel.DIESEL, Transmission.MANUAL),
            (
                "4567DEF",
                "Toyota",
                "Corolla",
                "compacto-aut",
                pmi,
                Fuel.HYBRID,
                Transmission.AUTOMATIC,
            ),
            ("5678EFG", "Nissan", "Qashqai", "suv", palma, Fuel.HYBRID, Transmission.AUTOMATIC),
        ]
        for matricula, marca, modelo, categoria, oficina, combustible, cambio in flota:
            _, creado = Vehicle.objects.get_or_create(
                plate=matricula,
                defaults={
                    "brand": marca,
                    "model": modelo,
                    "category": VehicleCategory.objects.get(code=categoria),
                    "current_office": oficina,
                    "fuel": combustible,
                    "transmission": cambio,
                    "mileage": 25_000,
                },
            )
            if creado:
                self.stdout.write(f"Vehiculo creado: {matricula} {marca} {modelo}")

    def _crear_extras(self):
        from decimal import Decimal

        from apps.pricing.models import CalculationType, Extra

        extras = [
            ("silla-bebe", "Silla de bebe", CalculationType.PER_DAY, "5.00", "50.00", 2, False, 10),
            ("gps", "GPS", CalculationType.PER_DAY, "6.00", "60.00", 1, False, 20),
            (
                "conductor2",
                "Segundo conductor",
                CalculationType.PER_RESERVATION,
                "25.00",
                None,
                3,
                True,
                30,
            ),
            (
                "entrega",
                "Entrega fuera de horario",
                CalculationType.ONCE,
                "35.00",
                None,
                1,
                False,
                40,
            ),
        ]
        for code, nombre, cobro, precio, tope, maximo, conductor, orden in extras:
            _, creado = Extra.objects.get_or_create(
                code=code,
                defaults={
                    "name": nombre,
                    "calculation_type": cobro,
                    "price": Decimal(precio),
                    "max_amount": Decimal(tope) if tope else None,
                    "max_quantity": maximo,
                    "requires_driver_data": conductor,
                    "sort_order": orden,
                },
            )
            if creado:
                self.stdout.write(f"Extra creado: {nombre}")

    def _crear_clientes(self):
        from datetime import date

        from apps.customers.models import Customer, DocumentType
        from apps.offices.models import Office

        palma = Office.objects.get(code="palma")
        clientes = [
            ("Maria", "Gonzalez Perez", DocumentType.DNI, "12345678Z", date(1990, 5, 12)),
            ("John", "Smith", DocumentType.PASSPORT, "AB1234567", date(1985, 3, 2)),
            ("Ana", "Ramirez Coll", DocumentType.DNI, "00000000T", date(2003, 11, 30)),
        ]
        for nombre, apellidos, tipo, documento, nacimiento in clientes:
            _, creado = Customer.objects.get_or_create(
                document_type=tipo,
                document_number=documento,
                defaults={
                    "first_name": nombre,
                    "last_name": apellidos,
                    "birth_date": nacimiento,
                    "office": palma,
                    "phone": "600000000",
                },
            )
            if creado:
                self.stdout.write(f"Cliente creado: {nombre} {apellidos}")

    def _crear_tarifas(self):
        """Temporadas, tarifas con tramos y suplementos de ejemplo.

        Los tramos son los tipicos del sector: cuanto mas largo el alquiler,
        mas barato el dia. El motor los lee tal cual (ver docs/motor-tarifas.md).
        """
        from datetime import date, time
        from decimal import Decimal

        from apps.fleet.models import VehicleCategory
        from apps.offices.models import Office
        from apps.pricing.models import (
            AmountType,
            Channel,
            Rate,
            RateTier,
            Season,
            Supplement,
            SupplementType,
        )

        temporadas = [
            ("baja", "Temporada baja", date(2026, 11, 1), date(2027, 3, 31), 0),
            ("media", "Temporada media", date(2026, 4, 1), date(2026, 6, 30), 0),
            ("alta", "Temporada alta", date(2026, 7, 1), date(2026, 10, 31), 0),
        ]
        for code, nombre, desde, hasta, prioridad in temporadas:
            _, creada = Season.objects.get_or_create(
                code=code,
                defaults={
                    "name": nombre,
                    "start_date": desde,
                    "end_date": hasta,
                    "priority": prioridad,
                },
            )
            if creada:
                self.stdout.write(f"Temporada creada: {nombre}")

        # Precio por dia del tramo 1 dia, por categoria y temporada. Los demas
        # tramos salen de aplicarle el descuento habitual por duracion.
        precios = {
            "eco": {"baja": "29.00", "media": "39.00", "alta": "55.00"},
            "compacto": {"baja": "35.00", "media": "45.00", "alta": "65.00"},
            "compacto-aut": {"baja": "42.00", "media": "52.00", "alta": "72.00"},
            "suv": {"baja": "55.00", "media": "70.00", "alta": "95.00"},
            "furgon": {"baja": "65.00", "media": "80.00", "alta": "110.00"},
        }
        #: (desde, hasta, % del precio de un dia)
        escalones = [
            (1, 1, Decimal("1.00")),
            (2, 3, Decimal("0.90")),
            (4, 7, Decimal("0.80")),
            (8, 14, Decimal("0.70")),
            (15, None, Decimal("0.60")),
        ]

        for categoria_code, por_temporada in precios.items():
            categoria = VehicleCategory.objects.get(code=categoria_code)
            for temporada_code, precio_dia in por_temporada.items():
                temporada = Season.objects.get(code=temporada_code)
                tarifa, creada = Rate.objects.get_or_create(
                    code=f"{categoria_code}-{temporada_code}",
                    defaults={
                        "name": f"{categoria.name} · {temporada.name}",
                        "channel": Channel.COUNTER,
                        "season": temporada,
                        "priority": 0,
                    },
                )
                if not creada:
                    continue
                tarifa.categories.set([categoria])
                for desde, hasta, factor in escalones:
                    RateTier.objects.create(
                        rate=tarifa,
                        min_days=desde,
                        max_days=hasta,
                        price_per_day=(Decimal(precio_dia) * factor).quantize(Decimal("0.01")),
                    )
                self.stdout.write(f"Tarifa creada: {tarifa.name}")

        suplementos = [
            {
                "code": "one-way",
                "name": "Devolucion en otra oficina",
                "supplement_type": SupplementType.ONE_WAY,
                "amount_type": AmountType.FIXED,
                "amount": Decimal("45.00"),
            },
            {
                "code": "conductor-joven",
                "name": "Conductor joven",
                "supplement_type": SupplementType.YOUNG_DRIVER,
                "amount_type": AmountType.PERCENT,
                "amount": Decimal("15.00"),
                "min_age": 18,
                "max_age": 24,
            },
            {
                "code": "fuera-de-horario",
                "name": "Entrega o devolucion fuera de horario",
                "supplement_type": SupplementType.AFTER_HOURS,
                "amount_type": AmountType.FIXED,
                "amount": Decimal("35.00"),
                "hours_from": time(8, 0),
                "hours_to": time(20, 0),
            },
            {
                "code": "aeropuerto",
                "name": "Tasa de aeropuerto",
                "supplement_type": SupplementType.AIRPORT,
                "amount_type": AmountType.PERCENT,
                "amount": Decimal("12.00"),
            },
        ]
        for datos in suplementos:
            suplemento, creado = Supplement.objects.get_or_create(
                code=datos.pop("code"), defaults=datos
            )
            if creado:
                if suplemento.supplement_type == SupplementType.AIRPORT:
                    suplemento.offices.set(Office.objects.filter(code="pmi"))
                self.stdout.write(f"Suplemento creado: {suplemento.name}")
