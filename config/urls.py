from django.contrib import admin
from django.urls import include, path

from apps.core.views import health

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
]
