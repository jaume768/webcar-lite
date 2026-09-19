"""Navegacion lateral. Cada app ira anadiendo su seccion aqui."""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    # Trazado del icono (atributo `d` de un <path> de 24x24, trazo de 1.5).
    icon: str
    #: Permiso necesario para ver la entrada. Ocultarla no sustituye a validar
    #: en la vista; es solo para no ensenar puertas cerradas.
    permission: str = ""


@dataclass(frozen=True)
class NavSection:
    label: str
    items: list[NavItem] = field(default_factory=list)


ICON_HOME = "M2.25 12l8.954-8.955a1.126 1.126 0 011.591 0L21.75 12M4.5 9.75v10.5a1.125 1.125 0 001.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21.375h4.125A1.125 1.125 0 0019.5 20.25V9.75"  # noqa: E501
ICON_CASH = "M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 004.5 19.5z"  # noqa: E501
ICON_KEY = "M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"  # noqa: E501
ICON_COG = "M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z M15 12a3 3 0 11-6 0 3 3 0 016 0z"  # noqa: E501
ICON_USERS = "M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"  # noqa: E501
ICON_SWATCH = "M4.098 19.902a3.75 3.75 0 005.304 0l6.401-6.402M6.75 21A3.75 3.75 0 013 17.25V4.125C3 3.504 3.504 3 4.125 3h5.25c.621 0 1.125.504 1.125 1.125v4.072M6.75 21a3.75 3.75 0 003.75-3.75V8.197M6.75 21h13.125c.621 0 1.125-.504 1.125-1.125v-5.25c0-.621-.504-1.125-1.125-1.125h-4.072M10.5 8.197l2.88-2.88c.438-.439 1.15-.439 1.59 0l3.712 3.713c.44.44.44 1.152 0 1.59l-2.879 2.88M6.75 17.25h.008v.008H6.75v-.008z"  # noqa: E501

ICON_BUILDING = "M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21"  # noqa: E501
ICON_MAP = "M9 6.75V15m6-6v8.25m.503 3.498l4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 00-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0z"  # noqa: E501
ICON_CASH = "M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 004.5 19.5z"  # noqa: E501
ICON_KEY = "M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"  # noqa: E501
ICON_LOCK = "M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"  # noqa: E501
ICON_CALENDAR = "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5"  # noqa: E501
ICON_CALC = "M15.75 15.75V18m-7.5-6.75h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25V13.5zm0 2.25h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25V18zm2.498-6.75h.007v.008h-.007v-.008zm0 2.25h.007v.008h-.007V13.5zm0 2.25h.007v.008h-.007v-.008zm0 2.25h.007v.008h-.007V18zm2.504-6.75h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V13.5zm0 2.25h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V18zm2.498-6.75h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V13.5zM8.25 6h7.5v2.25h-7.5V6zM12 2.25c-1.892 0-3.758.11-5.593.322C5.307 2.7 4.5 3.65 4.5 4.757V19.5a2.25 2.25 0 002.25 2.25h10.5a2.25 2.25 0 002.25-2.25V4.757c0-1.108-.806-2.057-1.907-2.185A48.507 48.507 0 0012 2.25z"  # noqa: E501
ICON_WARNING = "M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"  # noqa: E501
ICON_TAG = "M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.57 3zM6 6h.008v.008H6V6z"  # noqa: E501
ICON_CAR = "M8.25 18.75a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m3 0h6m-9 0H3.375a1.125 1.125 0 01-1.125-1.125V14.25m17.25 4.5a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m3 0h1.125c.621 0 1.129-.504 1.09-1.124a17.902 17.902 0 00-3.213-9.193 2.056 2.056 0 00-1.58-.86H14.25M16.5 18.75h-2.25m0-11.177v-.958c0-.568-.422-1.048-.987-1.106a48.554 48.554 0 00-9.026 0A1.106 1.106 0 003.25 6.615v9.017m11-8.06H2.25"  # noqa: E501

ICON_PIN = "M15 10.5a3 3 0 11-6 0 3 3 0 016 0z M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1115 0z"  # noqa: E501
ICON_PLUS_CIRCLE = "M12 9v6m3-3H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z"
ICON_PERCENT = "M9 15l6-6M9.75 9.75h.008v.008H9.75V9.75zm4.5 4.5h.008v.008h-.008v-.008zM21 12a9 9 0 11-18 0 9 9 0 0118 0z"  # noqa: E501
ICON_USER_CIRCLE = "M17.982 18.725A7.488 7.488 0 0012 15.75a7.488 7.488 0 00-5.982 2.975m11.963 0a9 9 0 10-11.963 0m11.963 0A8.966 8.966 0 0112 21a8.966 8.966 0 01-5.982-2.275M15 9.75a3 3 0 11-6 0 3 3 0 016 0z"  # noqa: E501
ICON_DOC = "M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"  # noqa: E501

MAIN_NAV: list[NavSection] = [
    NavSection(
        label=_("General"),
        items=[
            NavItem(label=_("Inicio"), url_name="core:home", icon=ICON_HOME),
        ],
    ),
    NavSection(
        label=_("Operativa"),
        items=[
            NavItem(
                label=_("Reservas"),
                url_name="reservations:list",
                icon=ICON_CALENDAR,
                permission="reservations.view_reservation",
            ),
            NavItem(
                label=_("Clientes"),
                url_name="customers:customer_list",
                icon=ICON_USER_CIRCLE,
                permission="customers.view_customer",
            ),
            NavItem(
                label=_("Arqueo de caja"),
                url_name="billing:cash_register",
                icon=ICON_CASH,
                permission="billing.view_billing",
            ),
        ],
    ),
    NavSection(
        label=_("Flota"),
        items=[
            NavItem(
                label=_("Vehículos"),
                url_name="fleet:vehicle_list",
                icon=ICON_CAR,
                permission="fleet.view_vehicle",
            ),
            NavItem(
                label=_("Categorías"),
                url_name="fleet:category_list",
                icon=ICON_TAG,
                permission="fleet.view_vehiclecategory",
            ),
            NavItem(
                label=_("Bloqueos"),
                url_name="fleet:block_list",
                icon=ICON_LOCK,
                permission="fleet.view_vehicleblock",
            ),
        ],
    ),
    NavSection(
        label=_("Tarifas"),
        items=[
            NavItem(
                label=_("Tarifas"),
                url_name="pricing:rate_list",
                icon=ICON_DOC,
                permission="pricing.view_rate",
            ),
            NavItem(
                label=_("Temporadas"),
                url_name="pricing:season_list",
                icon=ICON_CALENDAR,
                permission="pricing.view_season",
            ),
            NavItem(
                label=_("Extras"),
                url_name="pricing:extra_list",
                icon=ICON_PLUS_CIRCLE,
                permission="pricing.view_extra",
            ),
            NavItem(
                label=_("Suplementos"),
                url_name="pricing:supplement_list",
                icon=ICON_PERCENT,
                permission="pricing.view_supplement",
            ),
            NavItem(
                label=_("Descuentos"),
                url_name="pricing:discount_list",
                icon=ICON_TAG,
                permission="pricing.view_discount",
            ),
            NavItem(
                label=_("Simulador"),
                url_name="pricing:simulator",
                icon=ICON_CALC,
                permission="pricing.view_rate",
            ),
            NavItem(
                label=_("Conflictos"),
                url_name="pricing:rate_conflicts",
                icon=ICON_WARNING,
                permission="pricing.view_rate",
            ),
        ],
    ),
    NavSection(
        label=_("Administración"),
        items=[
            NavItem(
                label=_("Oficinas"),
                url_name="offices:office_list",
                icon=ICON_PIN,
                permission="offices.view_office",
            ),
            NavItem(
                label=_("Grupos de oficinas"),
                url_name="offices:pool_list",
                icon=ICON_MAP,
                permission="offices.view_officepool",
            ),
            NavItem(
                label=_("Usuarios"),
                url_name="accounts:user_list",
                icon=ICON_USERS,
                permission="accounts.manage_users",
            ),
            NavItem(
                label=_("Configuración"),
                url_name="settings_app:settings",
                icon=ICON_COG,
                permission="settings_app.access_settings",
            ),
            # Referencia del sistema de interfaz: solo para quien administra.
            NavItem(
                label=_("Componentes"),
                url_name="core:ui_kit",
                icon=ICON_SWATCH,
                permission="settings_app.access_settings",
            ),
        ],
    ),
]


def sections_for(user) -> list[NavSection]:
    """Secciones que el usuario puede ver. Una seccion vacia no se pinta."""
    visibles = []
    for section in MAIN_NAV:
        items = [
            item
            for item in section.items
            if not item.permission or (user and user.has_perm(item.permission))
        ]
        if items:
            visibles.append(NavSection(label=section.label, items=items))
    return visibles
