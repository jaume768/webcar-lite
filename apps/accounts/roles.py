"""Roles del sistema y el conjunto de permisos que reparte cada uno.

Es la referencia: `manage.py sync_roles` la aplica sobre la base de datos.
Los permisos se nombran como `app_label.codename`, incluidos los de apps que
todavia no tienen modelos: sync_roles avisa de los que faltan y los asigna solos
en cuanto existan.
"""

from dataclasses import dataclass, field

# --- Permisos transversales -------------------------------------------------
GESTION_USUARIOS = "accounts.manage_users"
CONFIGURACION = "settings_app.access_settings"

# --- Reservas ---------------------------------------------------------------
VER_RESERVAS = "reservations.view_reservation"
CREAR_RESERVAS = "reservations.add_reservation"
EDITAR_RESERVAS = "reservations.change_reservation"
CANCELAR_RESERVA = "reservations.cancel_reservation"
BORRAR_RESERVA = "reservations.delete_reservation"
CAMBIAR_PRECIO = "reservations.change_reservation_price"
FORZAR_DISPONIBILIDAD = "availability.override_availability"

# --- Dinero -----------------------------------------------------------------
VER_FACTURACION = "billing.view_billing"
COBRAR = "billing.add_payment"
GESTIONAR_TARIFAS = "pricing.manage_rates"

# --- Maestros ---------------------------------------------------------------
VER_CLIENTES = "customers.view_customer"
CREAR_CLIENTES = "customers.add_customer"
EDITAR_CLIENTES = "customers.change_customer"
VER_FLOTA = "fleet.view_vehicle"
EDITAR_FLOTA = "fleet.change_vehicle"
VER_OFICINAS = "offices.view_office"

CONSULTA = [VER_RESERVAS, VER_CLIENTES, VER_FLOTA, VER_OFICINAS]

MOSTRADOR = [
    *CONSULTA,
    CREAR_RESERVAS,
    EDITAR_RESERVAS,
    CREAR_CLIENTES,
    EDITAR_CLIENTES,
    COBRAR,
]

RESPONSABLE = [
    *MOSTRADOR,
    CANCELAR_RESERVA,
    CAMBIAR_PRECIO,
    FORZAR_DISPONIBILIDAD,
    VER_FACTURACION,
    EDITAR_FLOTA,
]

ADMINISTRACION = [
    *RESPONSABLE,
    BORRAR_RESERVA,
    GESTIONAR_TARIFAS,
    GESTION_USUARIOS,
    CONFIGURACION,
    "accounts.view_user",
    "accounts.add_user",
    "accounts.change_user",
    "accounts.view_role",
    "offices.add_office",
    "offices.change_office",
]


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    description: str
    permissions: list[str] = field(default_factory=list)


ROLE_SPECS: list[RoleSpec] = [
    RoleSpec(
        code="consulta",
        name="Consulta",
        description="Solo lectura. No modifica nada.",
        permissions=CONSULTA,
    ),
    RoleSpec(
        code="mostrador",
        name="Agente de mostrador",
        description="Opera reservas y clientes del dia a dia y registra cobros.",
        permissions=MOSTRADOR,
    ),
    RoleSpec(
        code="responsable",
        name="Responsable de oficina",
        description="Lo de mostrador mas cancelar, tocar precios y forzar disponibilidad.",
        permissions=RESPONSABLE,
    ),
    RoleSpec(
        code="administracion",
        name="Administracion",
        description="Gestiona usuarios, tarifas y configuracion. No es superusuario.",
        permissions=ADMINISTRACION,
    ),
]

ROLE_SPECS_BY_CODE = {spec.code: spec for spec in ROLE_SPECS}
