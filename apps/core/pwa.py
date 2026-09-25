"""App instalable (PWA): manifest, service worker y pagina sin conexion.

Las tres son publicas: el navegador las pide sin sesion y ninguna lee datos
del negocio. El service worker solo guarda estaticos y la pagina sin conexion;
nunca una pantalla con reservas, clientes o importes.
"""

import hashlib
import json

from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

# Lo que hace falta para pintar la pagina sin conexion sin red.
PRECACHE = [
    "css/app.css",
    "fonts/inter-latin.woff2",
    "img/pwa/icon.svg",
    "img/pwa/icon-192.png",
]

THEME_COLOR = "#254deb"
BACKGROUND_COLOR = "#ffffff"


@login_not_required
@require_GET
@cache_control(max_age=3600)
def manifest(request):
    return JsonResponse(
        {
            "id": "/",
            "name": "RentFlow",
            "short_name": "RentFlow",
            "description": _(
                "Gestión de alquiler de coches: reservas, entregas, devoluciones y caja."
            ),
            "lang": "es",
            "dir": "ltr",
            "start_url": reverse("core:home"),
            "scope": "/",
            "display": "standalone",
            "background_color": BACKGROUND_COLOR,
            "theme_color": THEME_COLOR,
            "categories": ["business", "productivity"],
            "icons": [
                {"src": static("img/pwa/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": static("img/pwa/icon-512.png"), "sizes": "512x512", "type": "image/png"},
                {
                    "src": static("img/pwa/icon-maskable-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {"src": static("img/pwa/icon.svg"), "sizes": "any", "type": "image/svg+xml"},
            ],
            "shortcuts": [
                {"name": _("Nueva reserva"), "url": reverse("reservations:quick")},
                {"name": _("Reservas"), "url": reverse("reservations:list")},
                {"name": _("Planning"), "url": reverse("reservations:planning")},
            ],
        },
        content_type="application/manifest+json",
        json_dumps_params={"ensure_ascii": False},
    )


@login_not_required
@require_GET
@cache_control(no_cache=True)
def service_worker(request):
    urls = [reverse("offline")] + [static(ruta) for ruta in PRECACHE]
    # Con whitenoise las URLs llevan el hash del contenido: si cambia un
    # estatico cambia la version, y el navegador instala el service worker nuevo.
    version = hashlib.sha1("|".join(urls).encode()).hexdigest()[:10]
    response = render(
        request,
        "core/sw.js",
        {
            "version": version,
            "urls_json": json.dumps(urls),
            "offline_url_json": json.dumps(urls[0]),
        },
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    return response


@login_not_required
@require_GET
def offline(request):
    """La guarda el service worker: sin nada de la sesion (ni usuario ni CSRF)."""
    return render(request, "core/offline.html")
