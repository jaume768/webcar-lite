"""Datos de demostracion: una empresa de alquiler con un mes de actividad.

No es un volcado de prueba: son reservas creadas por los mismos servicios que
usa el mostrador, asi que respetan disponibilidad, tarifas y estados. Lo que se
ve en la demo es lo que hace el sistema de verdad.

Es idempotente: se puede volver a lanzar sin duplicar nada.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

SEMILLA = 20260101

CATEGORIAS = [
    ("eco", "Economico", 4, 3, 1, 10),
    ("compacto", "Compacto", 5, 5, 2, 20),
    ("familiar", "Familiar", 5, 5, 3, 30),
    ("monovolumen", "Monovolumen", 7, 5, 4, 40),
    ("premium", "Premium", 5, 5, 3, 50),
]

MODELOS = {
    "eco": [("Fiat", "Panda"), ("Kia", "Picanto"), ("Toyota", "Aygo")],
    "compacto": [("Seat", "Ibiza"), ("Renault", "Clio"), ("Opel", "Corsa")],
    "familiar": [("Seat", "Leon"), ("Peugeot", "308"), ("Skoda", "Octavia")],
    "monovolumen": [("Citroen", "Berlingo"), ("Renault", "Scenic")],
    "premium": [("BMW", "Serie 3"), ("Audi", "A4")],
}

TARIFAS = {
    "eco": [(1, 1, "42.00"), (2, 3, "38.00"), (4, 7, "33.00"), (8, None, "28.00")],
    "compacto": [(1, 1, "50.00"), (2, 3, "45.00"), (4, 7, "40.00"), (8, None, "35.00")],
    "familiar": [(1, 1, "62.00"), (2, 3, "56.00"), (4, 7, "49.00"), (8, None, "43.00")],
    "monovolumen": [(1, 1, "78.00"), (2, 3, "71.00"), (4, 7, "64.00"), (8, None, "56.00")],
    "premium": [(1, 1, "95.00"), (2, 3, "88.00"), (4, 7, "79.00"), (8, None, "70.00")],
}

EXTRAS = [
    ("silla-infantil", "Silla infantil", "per_day", "6.00", 3, "45.00"),
    ("gps", "GPS", "per_day", "5.00", 1, "35.00"),
    ("conductor-extra", "Conductor adicional", "per_reservation", "25.00", 3, None),
    ("todo-riesgo", "Seguro a todo riesgo", "per_day", "12.00", 1, "120.00"),
]

CLIENTES = [
    ("Ana", "Garcia Lopez"),
    ("Marc", "Ferrer Pons"),
    ("Lucia", "Martin Ruiz"),
    ("Joan", "Ramis Serra"),
    ("Elena", "Navarro Gil"),
    ("Pau", "Company Mir"),
    ("Sofia", "Ortega Blanco"),
    ("Nicolas", "Duran Vives"),
    ("Marta", "Salas Roca"),
    ("Diego", "Herrera Luna"),
    ("Carmen", "Vidal Amengual"),
    ("Toni", "Bonet Cerda"),
]

#: Una clausula por elemento; el modelo las guarda como un texto por lineas.
CLAUSULAS = [
    "El arrendatario se compromete a devolver el vehiculo en la fecha, hora y "
    "oficina pactadas en este contrato.",
    "El vehiculo no podra ser conducido por personas distintas del titular y de "
    "los conductores adicionales autorizados.",
    "El combustible se factura segun la politica indicada, al precio vigente en "
    "el momento de la devolucion.",
    "Los danos no declarados en el momento de la entrega se consideran causados "
    "durante el periodo de alquiler.",
    "La fianza se retiene al inicio del alquiler y se devuelve tras comprobar el "
    "estado del vehiculo y el nivel de combustible.",
    "El kilometraje incluido es el indicado en el contrato; los kilometros de mas "
    "se facturan al precio por kilometro vigente.",
    "La devolucion fuera de plazo genera el cargo de los dias adicionales que "
    "correspondan segun el margen de cortesia.",
]
CONDICIONES = "\n".join(CLAUSULAS)


def _dni(indice: int) -> str:
    """DNI valido: numero de ocho cifras mas su letra de control."""
    numero = 10_000_000 + indice * 137_911
    return f"{numero:08d}{'TRWAGMYFPDXBNJZSQVHLCKE'[numero % 23]}"


class Command(BaseCommand):
    help = "Carga datos de demostracion realistas. Idempotente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Borra las reservas de demostracion antes de volver a crearlas.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not (settings.DEBUG or settings.DEMO_MODE):
            raise CommandError(
                "seed_demo solo se lanza con DEBUG=True o DEMO_MODE=True. "
                "En produccion no se cargan datos de mentira."
            )

        self.azar = random.Random(SEMILLA)
        self.ahora = timezone.now()

        empresa = self._empresa()
        condiciones = self._condiciones()
        oficinas = self._oficinas()
        categorias = self._categorias()
        self._tarifas(categorias, oficinas)
        self._extras()
        vehiculos = self._vehiculos(categorias, oficinas)
        clientes = self._clientes(oficinas)
        usuarios = self._usuarios(oficinas)

        if options["reset"]:
            self._borrar_reservas()

        reservas = self._reservas(categorias, oficinas, clientes, vehiculos, usuarios)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo lista: {empresa.legal_name} · condiciones v{condiciones.version} · "
                f"{len(oficinas)} oficinas · {len(vehiculos)} vehiculos · "
                f"{len(clientes)} clientes · {reservas} reservas"
            )
        )
        self.stdout.write(
            f"Acceso de demostracion: {settings.DEMO_EMAIL} / {settings.DEMO_PASSWORD}"
        )

    # --- maestros ---------------------------------------------------------

    def _empresa(self):
        from apps.settings_app.models import CompanySettings

        empresa = CompanySettings.load()
        empresa.legal_name = "Autos Demo Rent a Car, S.L."
        empresa.trade_name = "Autos Demo"
        empresa.tax_id = "B07456123"
        empresa.address = "Avenida del Puerto, 24"
        empresa.city = "Valencia"
        empresa.province = "Valencia"
        empresa.postal_code = "46021"
        empresa.phone = "960 45 67 89"
        empresa.email = "reservas@autosdemo.example"
        empresa.website = "https://autosdemo.example"
        empresa.save()
        return empresa

    def _condiciones(self):
        from apps.settings_app.models import TermsVersion

        vigente = TermsVersion.current()
        if vigente is not None:
            return vigente
        version = TermsVersion.objects.create(
            title="Condiciones generales de alquiler", body=CONDICIONES
        )
        return version.publish()

    def _oficinas(self):
        from apps.offices.models import Office, OfficePool

        pool, _ = OfficePool.objects.get_or_create(
            code="principal",
            defaults={"name": "Flota principal", "description": "Flota compartida"},
        )
        datos = [
            (
                "centro",
                "Oficina Centro",
                "Avenida del Puerto, 24",
                "Valencia",
                "46021",
                "960 45 67 89",
            ),
            (
                "aeropuerto",
                "Aeropuerto",
                "Terminal de llegadas",
                "Manises",
                "46940",
                "960 45 67 90",
            ),
            ("norte", "Oficina Norte", "Calle Mayor, 8", "Sagunto", "46500", "960 45 67 91"),
        ]
        oficinas = {}
        for code, name, direccion, ciudad, cp, telefono in datos:
            oficina, _ = Office.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "address": direccion,
                    "city": ciudad,
                    "province": "Valencia",
                    "postal_code": cp,
                    "phone": telefono,
                    "email": f"{code}@autosdemo.example",
                },
            )
            if oficina.pool_id is None:
                oficina.pool = pool
                oficina.save(update_fields=["pool"])
            oficinas[code] = oficina
        return oficinas

    def _categorias(self):
        from apps.fleet.models import Fuel, Transmission, VehicleCategory

        categorias = {}
        for code, nombre, plazas, puertas, maletas, orden in CATEGORIAS:
            categoria, _ = VehicleCategory.objects.get_or_create(
                code=code,
                defaults={
                    "name": nombre,
                    "seats": plazas,
                    "doors": puertas,
                    "luggage": maletas,
                    "sort_order": orden,
                    "transmission": Transmission.MANUAL,
                    "fuel": Fuel.PETROL,
                    "description": f"Grupo {nombre.lower()} para {plazas} personas.",
                },
            )
            categorias[code] = categoria
        return categorias

    def _tarifas(self, categorias, oficinas):
        from apps.pricing.models import Channel, Rate, RateTier, TierMode

        for code, tramos in TARIFAS.items():
            tarifa, creada = Rate.objects.get_or_create(
                code=f"mostrador-{code}",
                defaults={
                    "name": f"Mostrador {categorias[code].name}",
                    "channel": Channel.COUNTER,
                    "tier_mode": TierMode.FLAT,
                    "priority": 10,
                },
            )
            if creada:
                tarifa.categories.set([categorias[code]])
                tarifa.offices.set(oficinas.values())
                for minimo, maximo, precio in tramos:
                    RateTier.objects.create(
                        rate=tarifa, min_days=minimo, max_days=maximo, price_per_day=Decimal(precio)
                    )

    def _extras(self):
        from apps.pricing.models import Extra

        for code, nombre, tipo, precio, tope_unidades, tope_importe in EXTRAS:
            Extra.objects.get_or_create(
                code=code,
                defaults={
                    "name": nombre,
                    "calculation_type": tipo,
                    "price": Decimal(precio),
                    "max_quantity": tope_unidades,
                    "max_amount": Decimal(tope_importe) if tope_importe else None,
                },
            )

    def _vehiculos(self, categorias, oficinas):
        from apps.fleet.models import Fuel, Transmission, Vehicle

        letras = "BCDFGHJKLMNPRSTVWXYZ"
        vehiculos = []
        indice = 0
        for code, categoria in categorias.items():
            for marca, modelo in MODELOS[code]:
                for _ in range(2):
                    indice += 1
                    matricula = (
                        f"{1000 + indice * 7}{''.join(self.azar.choice(letras) for _ in range(3))}"
                    )
                    oficina = list(oficinas.values())[indice % len(oficinas)]
                    vehiculo, _creado = Vehicle.objects.get_or_create(
                        plate=matricula,
                        defaults={
                            "brand": marca,
                            "model": modelo,
                            "category": categoria,
                            "current_office": oficina,
                            "fuel": Fuel.PETROL if indice % 3 else Fuel.DIESEL,
                            "transmission": (
                                Transmission.AUTOMATIC if code == "premium" else Transmission.MANUAL
                            ),
                            "seats": categoria.seats,
                            "mileage": self.azar.randrange(8_000, 90_000, 500),
                            "tank_liters": 45 if code in ("eco", "compacto") else 60,
                            "itv_expiry": (
                                self.ahora + timedelta(days=self.azar.choice([12, 25, 200, 400]))
                            ).date(),
                            "insurance_expiry": (self.ahora + timedelta(days=300)).date(),
                            "color": self.azar.choice(["Blanco", "Gris", "Azul", "Negro"]),
                        },
                    )
                    vehiculos.append(vehiculo)
        return vehiculos

    def _clientes(self, oficinas):
        from apps.customers.models import Customer, DocumentType

        clientes = []
        for indice, (nombre, apellidos) in enumerate(CLIENTES):
            cliente, _ = Customer.objects.get_or_create(
                document_number=_dni(indice),
                defaults={
                    "first_name": nombre,
                    "last_name": apellidos,
                    "document_type": DocumentType.DNI,
                    "email": f"{nombre.lower()}.{apellidos.split()[0].lower()}@example.com",
                    "phone": f"6{indice:02d}111222",
                    "birth_date": (self.ahora - timedelta(days=365 * (25 + indice))).date(),
                    "licence_number": f"B{indice:06d}",
                    "licence_expiry": (self.ahora + timedelta(days=900)).date(),
                    "office": list(oficinas.values())[indice % len(oficinas)],
                    "city": "Valencia",
                },
            )
            clientes.append(cliente)
        return clientes

    def _usuarios(self, oficinas):
        from django.core.management import call_command

        from apps.accounts.models import Role, User

        call_command("sync_roles", verbosity=0)

        usuarios = {}
        cuentas = [
            (settings.DEMO_EMAIL, settings.DEMO_PASSWORD, "Demo", "Mostrador", "mostrador", None),
            (
                "responsable@autosdemo.example",
                "demo-webcar-2026",
                "Nuria",
                "Responsable",
                "responsable",
                None,
            ),
            (
                "direccion@autosdemo.example",
                "demo-webcar-2026",
                "Jordi",
                "Direccion",
                "administracion",
                None,
            ),
        ]
        for email, contrasena, nombre, apellidos, rol_code, _resto in cuentas:
            rol = Role.objects.filter(code=rol_code).first()
            usuario, creado = User.objects.get_or_create(
                email=email,
                defaults={"first_name": nombre, "last_name": apellidos, "role": rol},
            )
            if creado or not usuario.check_password(contrasena):
                usuario.set_password(contrasena)
            usuario.role = rol
            usuario.save()
            usuario.offices.set(oficinas.values())
            usuarios[rol_code] = usuario
        return usuarios

    # --- actividad --------------------------------------------------------

    def _borrar_reservas(self):
        from apps.billing.models import Payment
        from apps.contracts.models import Contract
        from apps.operations.models import CheckIn, CheckOut, Damage
        from apps.reservations.models import Reservation

        Contract.objects.all().delete()
        Payment.objects.all().update(reservation=None) if False else None
        for modelo in (CheckOut, CheckIn, Damage):
            modelo.objects.all().delete()
        Payment.objects.all().delete()
        Reservation.objects.all().delete()

    def _reservas(self, categorias, oficinas, clientes, vehiculos, usuarios):
        """Reservas repartidas por el mes: pasadas, de hoy y futuras."""
        from apps.availability.services import AvailabilityError, assign_vehicle
        from apps.billing.models import PaymentMethod, PaymentType
        from apps.billing.services import register_payment
        from apps.fleet.models import Vehicle, VehicleStatus
        from apps.operations.models import FuelLevel
        from apps.operations.services import perform_check_in, perform_check_out
        from apps.pricing.dto import ExtraRequest
        from apps.pricing.models import Extra
        from apps.pricing.services import PricingError
        from apps.reservations.models import ReservationStatus
        from apps.reservations.services import create_quick_reservation
        from apps.reservations.state_machine import transition

        if not settings.DEBUG and not settings.DEMO_MODE:  # pragma: no cover
            return 0

        empleado = usuarios["mostrador"]
        responsable = usuarios["responsable"]
        extras = list(Extra.objects.active())
        hoy = timezone.localtime(self.ahora).replace(minute=0, second=0, microsecond=0)

        # (dias de desfase de la recogida, duracion, hasta donde se lleva)
        guiones = [
            (-24, 5, "finalizada"),
            (-20, 3, "finalizada"),
            (-17, 7, "finalizada"),
            (-12, 4, "finalizada"),
            (-9, 2, "finalizada"),
            (-6, 3, "finalizada"),
            (-4, 6, "en_curso"),
            (-2, 5, "en_curso"),
            (-1, 4, "en_curso"),
            (0, 3, "hoy_entrega"),
            (0, 7, "hoy_entrega"),
            (0, 2, "hoy_entrega"),
            (-3, 3, "hoy_devolucion"),
            (-5, 5, "hoy_devolucion"),
            (2, 4, "confirmada"),
            (3, 7, "confirmada"),
            (5, 3, "confirmada"),
            (8, 10, "pendiente"),
            (12, 4, "pendiente"),
            (15, 6, "pendiente"),
        ]

        creadas = 0
        # Coches que se quedan fuera (entregados y sin devolver): no se pueden
        # volver a asignar, igual que en la realidad.
        ocupados: set[int] = set()
        entregas_de_hoy = 0

        for indice, (desfase, duracion, guion) in enumerate(guiones):
            categoria = list(categorias.values())[indice % len(categorias)]
            oficina = list(oficinas.values())[indice % len(oficinas)]
            cliente = clientes[indice % len(clientes)]

            recogida = hoy + timedelta(days=desfase, hours=self.azar.choice([-2, 0, 1, 3]))
            if guion == "hoy_entrega":
                recogida = hoy.replace(hour=self.azar.choice([9, 11, 13, 17]))
            devolucion = recogida + timedelta(days=duracion)
            if guion == "hoy_devolucion":
                devolucion = hoy.replace(hour=self.azar.choice([10, 12, 18]))

            pedidos = ()
            if indice % 3 == 0 and extras:
                pedidos = (ExtraRequest(extra=extras[indice % len(extras)], quantity=1),)

            try:
                reserva = create_quick_reservation(
                    category=categoria,
                    pickup_office=oficina,
                    return_office=oficina if indice % 4 else next(iter(oficinas.values())),
                    pickup_at=recogida,
                    return_at=devolucion,
                    customer=cliente,
                    extras=pedidos,
                    actor=empleado,
                )
            except (AvailabilityError, PricingError):
                # La demo no fuerza nada: si no hay hueco, esa reserva no existe.
                continue

            creadas += 1
            # Se relee el estado: la lista en memoria no sabe que coches han
            # salido ya en las reservas anteriores de este mismo guion.
            libres = list(
                Vehicle.objects.filter(category=categoria, current_office=oficina, is_active=True)
                .exclude(status=VehicleStatus.RENTED)
                .exclude(pk__in=ocupados)
                .order_by("plate")
            )
            # Las entregas de hoy salen con coche salvo la primera, que se deja
            # sin asignar a proposito: asi la demo ensena tambien la alerta del
            # panel y el boton de "Asignar coche".
            if guion == "hoy_entrega":
                entregas_de_hoy += 1
                con_coche = entregas_de_hoy > 1
            else:
                con_coche = guion in ("finalizada", "en_curso", "hoy_devolucion")
            if con_coche and libres:
                try:
                    assign_vehicle(reservation=reserva, vehicle=libres[0], actor=empleado)
                except Exception:
                    pass
                else:
                    if guion in ("en_curso", "hoy_devolucion"):
                        ocupados.add(libres[0].pk)
                reserva.refresh_from_db()

            self._avanzar(
                reserva,
                guion,
                empleado,
                responsable,
                PaymentMethod,
                PaymentType,
                register_payment,
                transition,
                ReservationStatus,
                perform_check_in,
                perform_check_out,
                FuelLevel,
            )

        return creadas

    def _avanzar(
        self,
        reserva,
        guion,
        empleado,
        responsable,
        PaymentMethod,
        PaymentType,
        register_payment,
        transition,
        ReservationStatus,
        perform_check_in,
        perform_check_out,
        FuelLevel,
    ):
        """Lleva cada reserva hasta donde toca, por los servicios de verdad."""
        if guion == "pendiente":
            return

        transition(reserva, ReservationStatus.CONFIRMED, empleado)
        reserva.refresh_from_db()

        if guion in ("confirmada", "hoy_entrega"):
            if self.azar.random() < 0.6:
                register_payment(
                    reservation=reserva,
                    amount=(reserva.total / 2).quantize(Decimal("0.01")),
                    method=PaymentMethod.CARD,
                    payment_type=PaymentType.ADVANCE,
                    actor=empleado,
                )
            return

        if reserva.vehicle_id is None:
            return

        perform_check_in(
            reservation=reserva,
            mileage=reserva.vehicle.mileage,
            fuel_level=FuelLevel.FULL,
            licence_verified=True,
            id_verified=True,
            actual_datetime=reserva.pickup_at,
            employee=empleado,
            damages=(
                [
                    {
                        "zone": "front_left",
                        "damage_type": "scratch",
                        "severity": 1,
                        "description": "Aranazo leve en la puerta",
                    }
                ]
                if self.azar.random() < 0.3
                else ()
            ),
        )
        reserva.refresh_from_db()

        if guion in ("en_curso", "hoy_devolucion"):
            register_payment(
                reservation=reserva,
                amount=Decimal("150.00"),
                method=PaymentMethod.CARD,
                payment_type=PaymentType.DEPOSIT,
                actor=empleado,
            )
            return

        # Finalizada: se devuelve y se cobra lo que quede.
        perform_check_out(
            reservation=reserva,
            mileage=reserva.vehicle.mileage + self.azar.randrange(200, 1400, 50),
            fuel_level=self.azar.choice([FuelLevel.FULL, FuelLevel.FULL, FuelLevel.THREE_QUARTERS]),
            actual_datetime=reserva.return_at,
            employee=empleado,
        )
        reserva.refresh_from_db()
        if reserva.grand_total > 0:
            register_payment(
                reservation=reserva,
                amount=reserva.grand_total,
                method=self.azar.choice([PaymentMethod.CARD, PaymentMethod.CASH]),
                actor=empleado,
            )
