"""Vistas transversales. Aqui solo va lo que no pertenece a ningun dominio."""

import redis
import structlog
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import demo
from .htmx import trigger_toast
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


@login_not_required
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


#: Lo que se cuenta en la portada. Vive aqui y no en la plantilla para que el
#: texto de venta se pueda revisar sin abrir HTML. Poco y al grano: la portada
#: se lee en un movil y el detalle se descubre entrando en la demo.
MODULOS_LANDING = [
    _("Reservas"),
    _("Disponibilidad"),
    _("Tarifas"),
    _("Flota"),
    _("Clientes"),
    _("Cobros y caja"),
    _("Entregas y devoluciones"),
    _("Contratos"),
    _("Usuarios y permisos"),
]

#: (icono, titulo, texto). El icono es el nombre de un bloque SVG de la plantilla.
PASOS_LANDING = [
    ("calendario", _("Se reserva"), _("En mostrador o por teléfono, con precio al momento.")),
    ("coche", _("Se entrega"), _("Check-in rápido con toda la información.")),
    ("vuelta", _("Se devuelve"), _("Inspección y cierre en segundos.")),
    ("tarjeta", _("Se cobra"), _("Tarjeta, efectivo o transferencia, con la fianza aparte.")),
]

#: (ruta de la foto en static/, nombre, empresa, texto). Las empresas son de
#: ejemplo: cuando haya clientes reales que quieran aparecer, se sustituyen aqui.
TESTIMONIOS_LANDING = [
    (
        "img/landing/testimonio-1.webp",
        "Carlos Ramis",
        "Ramis Rent a Car",
        _(
            "RentFlow nos ha ahorrado mucho tiempo en el mostrador. "
            "Ahora todo es más rápido y organizado."
        ),
    ),
    (
        "img/landing/testimonio-2.webp",
        "Laura Ferrer",
        "AutoRent Levante",
        _("Muy fácil de usar y el soporte siempre responde. Se nota que conocen el sector."),
    ),
    (
        "img/landing/testimonio-3.webp",
        "Miguel Ángel Torres",
        "Costa Cars",
        _(
            "Controlamos toda la flota, reservas y cobros desde un solo sitio. "
            "Imprescindible para nuestro día a día."
        ),
    ),
]

#: (pregunta, respuesta). La primera se pinta abierta.
FAQ_LANDING = [
    (
        _("¿Es difícil de usar?"),
        _(
            "No. RentFlow está diseñado para ser intuitivo y fácil de usar. Además, te ofrecemos "
            "formación y soporte para que empieces sin complicaciones."
        ),
    ),
    (
        _("¿Puedo importar mis reservas actuales?"),
        _("Sí. Te ayudamos a pasar clientes, flota y reservas abiertas desde tu sistema anterior."),
    ),
    (
        _("¿Funciona en móviles y tablets?"),
        _(
            "Sí. La interfaz se adapta a cualquier pantalla, así que puedes trabajar "
            "fuera del mostrador."
        ),
    ),
    (
        _("¿Qué tipo de soporte ofrecéis?"),
        _(
            "Soporte en español por teléfono y correo, con la temporada alta cubierta "
            "también en fin de semana."
        ),
    ),
    (
        _("¿Cuánto cuesta?"),
        _(
            "Una cuota mensual por oficina, sin permanencia. Escríbenos y te pasamos "
            "el presupuesto."
        ),
    ),
]

#: Fotos de la portada, en static/. Los originales estan en fotos/ (PNG); aqui
#: van convertidas a WebP, que pesan diez veces menos.
FOTOS_LANDING = {
    "hero": "img/landing/hero.webp",
    "flota": "img/landing/flota.webp",
    "clientes": "img/landing/clientes.webp",
    "cobros": "img/landing/cobros.webp",
    "paisaje": "img/landing/paisaje.webp",
    "movilidad": "img/landing/movilidad.webp",
    "movil": "img/landing/movil.webp",
}


@login_not_required
def home(request):
    """Portada publica o panel de mostrador, segun quien mire.

    Es la misma URL a proposito: quien llega sin sesion ve la pagina que explica
    el producto, y quien ya ha entrado ve su trabajo del dia.

    Los datos los arma `operations.dashboard`, que es quien sabe de entregas y
    devoluciones. El import va dentro de la funcion a proposito: `core` es la
    base sobre la que se apoyan las demas apps y no debe depender de ellas al
    importarse.
    """
    if not request.user.is_authenticated:
        return render(
            request,
            "core/landing.html",
            {
                "modulos": MODULOS_LANDING,
                "pasos": PASOS_LANDING,
                # La foto se resuelve aqui por lo mismo que `fotos`.
                "testimonios": [
                    {"foto": static(foto), "nombre": nombre, "empresa": empresa, "texto": texto}
                    for foto, nombre, empresa, texto in TESTIMONIOS_LANDING
                ],
                "faqs": FAQ_LANDING,
                # Se resuelven en cada peticion y no al importar: en produccion
                # `static()` consulta el manifiesto de collectstatic.
                "fotos": {clave: static(ruta) for clave, ruta in FOTOS_LANDING.items()},
                "demo_activa": settings.DEMO_MODE,
                "demo_email": settings.DEMO_EMAIL,
                "demo_password": settings.DEMO_PASSWORD,
            },
        )

    from apps.offices.selectors import offices_for_user
    from apps.operations.dashboard import PERIODS, build_dashboard, period_for

    oficina = None
    elegida = request.GET.get("office")
    if elegida:
        oficina = offices_for_user(request.user).filter(pk=elegida).first()

    panel = build_dashboard(
        user=request.user,
        office=oficina,
        period=period_for(request.GET.get("periodo"), request.GET.get("dia")),
        # La caja se consulta solo para quien puede ver cobros: no basta con no
        # pintarla.
        include_cash=request.user.has_perm("billing.view_billing"),
    )

    contexto = {
        "page_title": _("Mostrador"),
        "panel": panel,
        "saludo": _saludo(timezone.localtime(panel.generated_at).hour),
        "periods": PERIODS.items(),
        "dia_elegido": panel.period.anchor(timezone.localdate()),
        "refresh_seconds": settings.DASHBOARD_REFRESH_SECONDS,
    }

    if request.htmx and not request.htmx.boosted:
        # Solo los datos: la cabecera se queda quieta y no se pierde el foco.
        return render(request, "core/_dashboard_panels.html", contexto)
    return render(request, "core/home.html", contexto)


def _saludo(hora: int) -> str:
    if 6 <= hora < 14:
        return _("Buenos días")
    if 14 <= hora < 21:
        return _("Buenas tardes")
    return _("Buenas noches")


@require_GET
def alerts_menu(request):
    """Avisos de la campana. Se piden al abrirla: ninguna pagina paga por ellos."""
    from apps.operations.dashboard import build_dashboard

    panel = build_dashboard(user=request.user)
    return render(request, "shell/_alerts_menu.html", {"alerts": panel.alerts})


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

    # Solo variantes conocidas: el valor acaba en un id del HTML.
    variante = "movil" if request.POST.get("variante") == "movil" else ""
    response = render(request, "shell/_office_selector.html", {"variante": variante})
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


@login_not_required
@require_POST
def demo_login(request):
    """Entra en la demostracion sin escribir credenciales.

    Solo existe con `DEMO_MODE` encendido. Con el apagado responde 404, que es
    lo que corresponde: en una instalacion real esta puerta no esta ahi.
    """
    if not settings.DEMO_MODE:
        raise Http404

    from django.contrib.auth import authenticate, login

    usuario = authenticate(request, username=settings.DEMO_EMAIL, password=settings.DEMO_PASSWORD)
    if usuario is None:
        logger.warning("demo_sin_datos", email=settings.DEMO_EMAIL)
        messages.error(
            request,
            _("La demostracion no esta preparada todavia. Ejecuta: manage.py seed_demo"),
        )
        return HttpResponseRedirect(reverse("accounts:login"))

    from apps.accounts.services import grant_demo_permissions

    # Una demo cargada antes de ampliar sus permisos tambien los recibe.
    grant_demo_permissions(usuario)
    login(request, usuario)
    logger.info("acceso_de_demostracion", user_id=usuario.pk)
    messages.info(
        request,
        _("Estas en la demostracion con datos de mentira. Mira, prueba y rompe lo que quieras."),
    )
    return HttpResponseRedirect(reverse("core:home"))
