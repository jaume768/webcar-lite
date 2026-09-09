"""Navegacion lateral. Cada app ira anadiendo su seccion aqui."""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    # Trazado del icono (atributo `d` de un <path> de 24x24, trazo de 1.5).
    icon: str


@dataclass(frozen=True)
class NavSection:
    label: str
    items: list[NavItem] = field(default_factory=list)


ICON_HOME = "M2.25 12l8.954-8.955a1.126 1.126 0 011.591 0L21.75 12M4.5 9.75v10.5a1.125 1.125 0 001.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21.375h4.125A1.125 1.125 0 0019.5 20.25V9.75"  # noqa: E501
ICON_SWATCH = "M4.098 19.902a3.75 3.75 0 005.304 0l6.401-6.402M6.75 21A3.75 3.75 0 013 17.25V4.125C3 3.504 3.504 3 4.125 3h5.25c.621 0 1.125.504 1.125 1.125v4.072M6.75 21a3.75 3.75 0 003.75-3.75V8.197M6.75 21h13.125c.621 0 1.125-.504 1.125-1.125v-5.25c0-.621-.504-1.125-1.125-1.125h-4.072M10.5 8.197l2.88-2.88c.438-.439 1.15-.439 1.59 0l3.712 3.713c.44.44.44 1.152 0 1.59l-2.879 2.88M6.75 17.25h.008v.008H6.75v-.008z"  # noqa: E501

MAIN_NAV: list[NavSection] = [
    NavSection(
        label=_("General"),
        items=[
            NavItem(label=_("Inicio"), url_name="core:home", icon=ICON_HOME),
            NavItem(label=_("Componentes"), url_name="core:ui_kit", icon=ICON_SWATCH),
        ],
    ),
]
