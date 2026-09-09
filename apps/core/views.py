"""Vistas transversales. Aqui solo va lo que no pertenece a ningun dominio."""

import redis
import structlog
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import demo
from .htmx import trigger_toast
from .offices import SESSION_OFFICES_KEY
from .offices import set_active_office as activar_oficina
from .tables import Column, Filter, FilterOption, Table, paginate

logger = structlog.get_logger(__name__)


def _check_database() -> tuple[bool, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # el healthcheck reporta el fallo, no lo propaga
        return False, str(exc)
    return True, "ok"


def _check_redis() -> tuple[bool, str]:
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except Exception as exc:  # el healthcheck reporta el fallo, no lo propaga
        return False, str(exc)
    finally:
        client.close()
    return True, "ok"


@never_cache
@require_GET
def health(request):
    """Estado de las dependencias externas. 200 si todo responde, 503 si no."""
    checks = {}
    for name, check in (("database", _check_database), ("redis", _check_redis)):
        ok, detail = check()
        checks[name] = {"ok": ok, "detail": detail}

    healthy = all(item["ok"] for item in checks.values())
    if not healthy:
        failed = [name for name, item in checks.items() if not item["ok"]]
        logger.error("healthcheck_failed", failed=failed)

    return JsonResponse(
        {"status": "ok" if healthy else "error", "checks": checks},
        status=200 if healthy else 503,
    )


def home(request):
    """Portada provisional del esqueleto. La sustituira el panel de mostrador."""
    return render(request, "core/home.html", {"page_title": _("Inicio")})


@require_POST
def set_active_office(request):
    """Cambia la oficina activa y devuelve el selector actualizado.

    La oficina llega del cliente, asi que se valida contra las permitidas: un
    POST manipulado no puede colocar una oficina ajena en la sesion.
    """
    try:
        office = activar_oficina(request, request.POST.get("office_id", ""))
    except ValueError:
        logger.warning("oficina_no_permitida", office_id=request.POST.get("office_id"))
        raise PermissionDenied(_("Esa oficina no esta disponible para tu usuario.")) from None

    response = render(request, "shell/_office_selector.html")
    return trigger_toast(response, _("Oficina activa: %s") % office.name, "success")


# ---------------------------------------------------------------------------
# Demostracion de componentes. Se borra cuando existan pantallas reales.
# ---------------------------------------------------------------------------

TAMANO_PAGINA_DEMO = 25


def _tabla_demo(request) -> Table:
    q = request.GET.get("q", "").strip()
    categoria = request.GET.get("categoria", "")
    estado = request.GET.get("estado", "")

    filas = demo.filtrar(q=q, categoria=categoria, estado=estado)

    return Table(
        id="tabla-vehiculos",
        url=reverse("core:ui_kit"),
        columns=[
            Column(label=_("Matricula"), css="font-mono tabular w-32"),
            Column(label=_("Modelo")),
            Column(label=_("Categoria")),
            Column(label=_("Oficina")),
            Column(label=_("Estado")),
            Column(label=_("Tarifa/dia"), align="right", css="tabular w-28"),
        ],
        page_obj=paginate(request, filas, TAMANO_PAGINA_DEMO),
        row_template="core/_ui_kit_row.html",
        search_value=q,
        search_placeholder=_("Matricula, modelo u oficina..."),
        filters=[
            Filter(
                name="categoria",
                label=_("Categoria"),
                value=categoria,
                options=[FilterOption(value=c, label=n) for c, n in demo.CATEGORIAS],
            ),
            Filter(
                name="estado",
                label=_("Estado"),
                value=estado,
                options=[FilterOption(value=c, label=n) for c, n, _t in demo.ESTADOS],
            ),
        ],
        empty_title=_("Ningun vehiculo coincide"),
        empty_message=_("Cambia la busqueda o quita algun filtro."),
    )


def ui_kit(request):
    """Catalogo de componentes. Sirve la pagina entera o solo la tabla."""
    tabla = _tabla_demo(request)

    # La tabla pide siempre esta misma URL; con HTMX solo devolvemos el trozo
    # que cambia, que es lo que evita recargar 10.000 filas de contexto.
    if request.htmx:
        return render(request, "ui/_table.html#resultados", {"table": tabla})

    # El selector de oficina necesita oficinas y el modelo Office aun no existe:
    # la demo las deja en la sesion, que es de donde las lee el proveedor.
    if request.session.get(SESSION_OFFICES_KEY) != demo.OFICINAS_DEMO:
        request.session[SESSION_OFFICES_KEY] = demo.OFICINAS_DEMO

    return render(
        request,
        "core/ui_kit.html",
        {
            "page_title": _("Componentes"),
            "page_subtitle": _("Referencia viva del sistema de interfaz."),
            "breadcrumbs": [
                {"label": _("Inicio"), "url": reverse("core:home")},
                {"label": _("Componentes")},
            ],
            "table": tabla,
            "form": demo.FormularioDemo(),
            "estados": demo.ESTADOS,
        },
    )


@require_GET
def ui_kit_vehicle_search(request):
    """Autocompletado del select con busqueda."""
    termino = request.GET.get("q", "").strip()
    resultados = demo.filtrar(q=termino)[:10] if termino else list(demo.catalogo()[:10])

    return render(
        request,
        "ui/_select_search_options.html",
        {
            "options": [
                {"value": v.id, "label": f"{v.plate} · {v.model}", "hint": v.office}
                for v in resultados
            ]
        },
    )


@require_GET
def ui_kit_modal(request):
    """Contenido de un modal cargado bajo demanda."""
    vehiculo = demo.catalogo()[0]
    return render(request, "core/_ui_kit_modal.html", {"vehiculo": vehiculo})


@require_POST
def ui_kit_destructive(request):
    """Accion destructiva de mentira: responde con el aviso correspondiente."""
    response = HttpResponse(status=204)
    return trigger_toast(response, _("Vehiculo dado de baja (es una demostracion)."), "success")


@require_GET
def ui_kit_error(request, code: int):
    """Muestra las paginas de error propias sin tener que provocar un fallo real."""
    if code == 403:
        raise PermissionDenied(_("Demostracion de la pagina 403."))
    if code == 404:
        raise Http404(_("Demostracion de la pagina 404."))
    if code == 500:
        # Se renderiza la plantilla tal cual: provocar una excepcion de verdad
        # ensuciaria los logs y, en produccion, dispararia las alertas.
        return render(request, "500.html", status=500)
    raise Http404
