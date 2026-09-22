"""Alta, renovacion de clave y baja de las webs que reservan por la API.

    manage.py api_client create "Web principal" --offices centro aeropuerto
    manage.py api_client rotate "Web principal"
    manage.py api_client deactivate "Web principal"
    manage.py api_client list

La clave se ensena una sola vez, al crearla o renovarla.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.booking_api import services
from apps.booking_api.models import ApiClient
from apps.offices.models import Office


class Command(BaseCommand):
    help = "Gestiona los clientes (webs) de la API de reservas."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="accion", required=True)
        crear = sub.add_parser("create")
        crear.add_argument("name")
        crear.add_argument("--offices", nargs="+", required=True, help="Codigos de oficina.")
        sub.add_parser("rotate").add_argument("name")
        sub.add_parser("deactivate").add_argument("name")
        sub.add_parser("list")

    def handle(self, *args, **options):
        accion = options["accion"]
        if accion == "list":
            for cliente in ApiClient.objects.select_related("user").prefetch_related(
                "user__offices"
            ):
                oficinas = ", ".join(o.code for o in cliente.user.offices.all())
                estado = "activo" if cliente.is_active else "baja"
                self.stdout.write(
                    f"{cliente.name}\t{estado}\trfk_{cliente.key_prefix}_…\t{oficinas}\t"
                    f"ultimo uso: {cliente.last_used_at or '—'}"
                )
            return

        if accion == "create":
            codigos = options["offices"]
            oficinas = list(Office.objects.filter(code__in=codigos, is_active=True))
            faltan = set(codigos) - {o.code for o in oficinas}
            if faltan:
                raise CommandError(f"Oficinas desconocidas o de baja: {', '.join(sorted(faltan))}")
            try:
                cliente, clave = services.create_client(name=options["name"], offices=oficinas)
            except services.BookingApiError as exc:
                raise CommandError(str(exc)) from exc
            self._ensenar(cliente, clave)
            return

        cliente = ApiClient.objects.filter(name=options["name"]).first()
        if cliente is None:
            raise CommandError(f"No hay ningun cliente de la API llamado {options['name']!r}.")
        if accion == "rotate":
            self._ensenar(cliente, services.rotate_key(client=cliente))
        elif accion == "deactivate":
            cliente.deactivate()
            self.stdout.write(self.style.SUCCESS(f"{cliente.name}: clave desactivada."))

    def _ensenar(self, cliente, clave):
        self.stdout.write(
            self.style.SUCCESS(f"{cliente.name}: guarda esta clave, no se vuelve a ver:")
        )
        self.stdout.write(clave)
