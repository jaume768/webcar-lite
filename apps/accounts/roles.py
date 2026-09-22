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
SOBREPAGO = "billing.allow_overpayment"
EMITIR_FACTURAS = "billing.add_invoice"
RECTIFICAR_FACTURAS = "billing.rectify_invoice"
GESTIONAR_SERIES = [
    "billing.view_invoiceseries",
    "billing.add_invoiceseries",
    "billing.change_invoiceseries",
]
GESTIONAR_TARIFAS = "pricing.manage_rates"

# --- Maestros ---------------------------------------------------------------
VER_CLIENTES = "customers.view_customer"
CREAR_CLIENTES = "customers.add_customer"
EDITAR_CLIENTES = "customers.change_customer"
VER_FLOTA = "fleet.view_vehicle"
EDITAR_FLOTA = "fleet.change_vehicle"
VER_CATEGORIAS = "fleet.view_vehiclecategory"
VER_BLOQUEOS = "fleet.view_vehicleblock"
CREAR_BLOQUEOS = "fleet.add_vehicleblock"
EDITAR_BLOQUEOS = "fleet.change_vehicleblock"
BORRAR_BLOQUEOS = "fleet.delete_vehicleblock"
VER_EXTRAS = "pricing.view_extra"
VER_TARIFAS = [
    "pricing.view_rate",
    "pricing.view_season",
    "pricing.view_supplement",
    "pricing.view_discount",
]
EDITAR_TARIFAS = [
    "pricing.add_rate",
    "pricing.change_rate",
    "pricing.add_season",
    "pricing.change_season",
    "pricing.add_supplement",
    "pricing.change_supplement",
    "pricing.add_discount",
    "pricing.change_discount",
]
VER_OFICINAS = "offices.view_office"
VER_GRUPOS = "offices.view_officepool"

CONSULTA = [
    VER_RESERVAS,
    VER_CLIENTES,
    VER_FLOTA,
    VER_CATEGORIAS,
    VER_BLOQUEOS,
    VER_EXTRAS,
    VER_OFICINAS,
    VER_GRUPOS,
]

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
    # Ve las tarifas para poder explicar un precio, pero no las cambia.
    *VER_TARIFAS,
    CREAR_BLOQUEOS,
    EDITAR_BLOQUEOS,
    BORRAR_BLOQUEOS,
    CANCELAR_RESERVA,
    CAMBIAR_PRECIO,
    FORZAR_DISPONIBILIDAD,
    VER_FACTURACION,
    EMITIR_FACTURAS,
    SOBREPAGO,
    EDITAR_FLOTA,
]

ADMINISTRACION = [
    *RESPONSABLE,
    BORRAR_RESERVA,
    # Anular una factura y la numeracion de las series son decisiones fiscales.
    RECTIFICAR_FACTURAS,
    *GESTIONAR_SERIES,
    GESTIONAR_TARIFAS,
    GESTION_USUARIOS,
    CONFIGURACION,
    "accounts.view_user",
    "accounts.add_user",
    "accounts.change_user",
    "accounts.view_role",
    "offices.add_office",
    "offices.change_office",
    "offices.add_officepool",
    "offices.change_officepool",
    "fleet.add_vehiclecategory",
    "fleet.change_vehiclecategory",
    "fleet.add_vehicle",
    "pricing.add_extra",
    "pricing.change_extra",
    *EDITAR_TARIFAS,
    # Politicas de la empresa: las que salen al pie de las facturas.
    "settings_app.view_policy",
    "settings_app.add_policy",
    "settings_app.change_policy",
]


#: Usuario tecnico de la API de reservas web (uno por web). Crea reservas y
#: clientes, cancela las suyas y pide enlaces de pago. No entra a la aplicacion:
#: no tiene contrasena utilizable. Que reservas puede tocar lo limita la propia
#: API (solo las que ella creo); en que oficinas, sus oficinas asignadas.
API_WEB = [
    CREAR_RESERVAS,
    CANCELAR_RESERVA,
    CREAR_CLIENTES,
    "billing.add_onlinepayment",
]


#: Lo que ve de mas la cuenta de demostracion, encima de su rol de mostrador:
#: todo lo de administracion, para que en la demo se pueda abrir cualquier
#: opcion del menu. No se toca el rol de mostrador, que es el que usan los
#: mostradores de verdad; estos permisos van solo a esa cuenta.
DEMO_EXTRA = list(ADMINISTRACION)


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
    RoleSpec(
        code="api_web",
        name="API de reservas web",
        description="Usuario tecnico de una web que reserva por la API. No entra a la aplicacion.",
        permissions=API_WEB,
    ),
]

ROLE_SPECS_BY_CODE = {spec.code: spec for spec in ROLE_SPECS}
