from django.urls import path

from . import views

app_name = "offices"

urlpatterns = [
    path("oficinas/", views.OfficeListView.as_view(), name="office_list"),
    path("oficinas/nueva/", views.OfficeCreateView.as_view(), name="office_create"),
    path("oficinas/<int:pk>/editar/", views.OfficeUpdateView.as_view(), name="office_update"),
    path(
        "oficinas/<int:pk>/activar/",
        views.OfficeActivateView.as_view(),
        name="office_activate",
    ),
    path(
        "oficinas/<int:pk>/desactivar/",
        views.OfficeDeactivateView.as_view(),
        name="office_deactivate",
    ),
    path("grupos-de-oficinas/", views.OfficePoolListView.as_view(), name="pool_list"),
    path("grupos-de-oficinas/nuevo/", views.OfficePoolCreateView.as_view(), name="pool_create"),
    path(
        "grupos-de-oficinas/<int:pk>/editar/",
        views.OfficePoolUpdateView.as_view(),
        name="pool_update",
    ),
    path(
        "grupos-de-oficinas/<int:pk>/activar/",
        views.OfficePoolActivateView.as_view(),
        name="pool_activate",
    ),
    path(
        "grupos-de-oficinas/<int:pk>/desactivar/",
        views.OfficePoolDeactivateView.as_view(),
        name="pool_deactivate",
    ),
]
