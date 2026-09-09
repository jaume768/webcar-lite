"""Tablas de datos: la configuracion que consume `ui/_table.html`.

La plantilla no calcula nada. La vista arma un `Table` y la plantilla lo pinta,
tanto en la carga completa como en el fragmento que devuelve HTMX.
"""

from dataclasses import dataclass, field

from django.core.paginator import EmptyPage, Page, PageNotAnInteger, Paginator

DEFAULT_PAGE_SIZE = 25


@dataclass(frozen=True)
class Column:
    label: str
    align: str = "left"
    #: Clases extra para las celdas de la columna (ancho, tipografia tabular...).
    css: str = ""


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str


@dataclass(frozen=True)
class Filter:
    name: str
    label: str
    options: list[FilterOption]
    value: str = ""


@dataclass
class Table:
    #: Id del contenedor que HTMX reemplaza. Unico por tabla en la pagina.
    id: str
    #: URL que sirve tanto la pagina completa como el fragmento.
    url: str
    columns: list[Column]
    page_obj: Page
    #: Plantilla que pinta las celdas de una fila. Recibe `row`.
    row_template: str
    search_value: str = ""
    search_placeholder: str = ""
    #: Filtros armados a mano por la vista (sin django-filter).
    filters: list[Filter] = field(default_factory=list)
    #: Formulario de un FilterSet de django-filter. El campo de busqueda `q` no
    #: se pinta aqui: ya lo sirve el buscador de la cabecera.
    filter_form: object = None
    #: Evento de servidor que obliga a la tabla a recargarse sola (ver
    #: core.crud.EVENTO_GUARDADO). Vacio: la tabla no escucha nada.
    refresh_event: str = ""
    #: URL con la que se recarga al recibir ese evento. Vacio: `url`. Se usa
    #: para conservar busqueda y filtros al refrescar.
    refresh_url: str = ""
    empty_title: str = ""
    empty_message: str = ""


def paginate(request, items, per_page: int = DEFAULT_PAGE_SIZE) -> Page:
    """Pagina una lista o queryset tolerando un ?page= invalido.

    Un numero de pagina roto en la URL no debe dar un 500 en mostrador: se cae
    a la primera o a la ultima pagina.
    """
    paginator = Paginator(items, per_page)
    numero = request.GET.get("page") or 1
    try:
        return paginator.page(numero)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)
