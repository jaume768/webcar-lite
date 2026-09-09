from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import registration_disabled
from apps.core.views import health

urlpatterns = [
    path("health/", health, name="health"),
    # El admin de Django es herramienta de soporte tecnico, no el panel de
    # gestion del cliente: ruta poco evidente y solo para staff.
    path("admin-interno/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.offices.urls")),
    path("", include("apps.fleet.urls")),
    path("", include("apps.customers.urls")),
    path("", include("apps.pricing.urls")),
    path("", include("apps.core.urls")),
]

# El alta publica no existe. Las rutas habituales responden 410 en lugar de un
# 404 que invite a seguir probando.
urlpatterns += [
    path(ruta, registration_disabled, name=f"registro_deshabilitado_{indice}")
    for indice, ruta in enumerate(
        ["registro/", "signup/", "register/", "alta/", "accounts/signup/"]
    )
]

# Imagenes subidas (fotos de categoria). En produccion las sirve el proxy.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
