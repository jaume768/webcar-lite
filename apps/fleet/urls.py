from django.urls import path

from . import views

app_name = "fleet"

urlpatterns = [
    # Categorias
    path("categorias/", views.VehicleCategoryListView.as_view(), name="category_list"),
    path("categorias/nueva/", views.VehicleCategoryCreateView.as_view(), name="category_create"),
    path(
        "categorias/<int:pk>/editar/",
        views.VehicleCategoryUpdateView.as_view(),
        name="category_update",
    ),
    path(
        "categorias/<int:pk>/activar/",
        views.VehicleCategoryActivateView.as_view(),
        name="category_activate",
    ),
    path(
        "categorias/<int:pk>/desactivar/",
        views.VehicleCategoryDeactivateView.as_view(),
        name="category_deactivate",
    ),
    # Vehiculos
    path("vehiculos/", views.VehicleListView.as_view(), name="vehicle_list"),
    path("vehiculos/nuevo/", views.VehicleCreateView.as_view(), name="vehicle_create"),
    path("vehiculos/<int:pk>/editar/", views.VehicleUpdateView.as_view(), name="vehicle_update"),
    path(
        "vehiculos/<int:pk>/situacion/",
        views.VehicleStatusUpdateView.as_view(),
        name="vehicle_status",
    ),
    path(
        "vehiculos/<int:pk>/activar/",
        views.VehicleActivateView.as_view(),
        name="vehicle_activate",
    ),
    path(
        "vehiculos/<int:pk>/desactivar/",
        views.VehicleDeactivateView.as_view(),
        name="vehicle_deactivate",
    ),
    # Bloqueos
    path("bloqueos/", views.VehicleBlockListView.as_view(), name="block_list"),
    path("bloqueos/nuevo/", views.VehicleBlockCreateView.as_view(), name="block_create"),
    path("bloqueos/<int:pk>/editar/", views.VehicleBlockUpdateView.as_view(), name="block_update"),
    path("bloqueos/<int:pk>/anular/", views.VehicleBlockDeleteView.as_view(), name="block_delete"),
]
