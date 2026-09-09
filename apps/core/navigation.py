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
ICON_USERS = "M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"  # noqa: E501
ICON_SWATCH = "M4.098 19.902a3.75 3.75 0 005.304 0l6.401-6.402M6.75 21A3.75 3.75 0 013 17.25V4.125C3 3.504 3.504 3 4.125 3h5.25c.621 0 1.125.504 1.125 1.125v4.072M6.75 21a3.75 3.75 0 003.75-3.75V8.197M6.75 21h13.125c.621 0 1.125-.504 1.125-1.125v-5.25c0-.621-.504-1.125-1.125-1.125h-4.072M10.5 8.197l2.88-2.88c.438-.439 1.15-.439 1.59 0l3.712 3.713c.44.44.44 1.152 0 1.59l-2.879 2.88M6.75 17.25h.008v.008H6.75v-.008z"  # noqa: E501

MAIN_NAV: list[NavSection] = [
    NavSection(
        label=_("General"),
        items=[
            NavItem(label=_("Inicio"), url_name="core:home", icon=ICON_HOME),
            NavItem(label=_("Componentes"), url_name="core:ui_kit", icon=ICON_SWATCH),
        ],
    ),
    NavSection(
        label=_("Administracion"),
        items=[
            NavItem(
                label=_("Usuarios"),
                url_name="accounts:user_list",
                icon=ICON_USERS,
                permission="accounts.manage_users",
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
