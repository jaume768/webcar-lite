from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("reservas/", views.ReservationListView.as_view(), name="list"),
    path("reservas/rapida/", views.QuickReservationView.as_view(), name="quick"),
    path("reservas/rapida/precio/", views.QuickReservationPreview.as_view(), name="quick_preview"),
    path("reservas/rapida/clientes/", views.CustomerSearchView.as_view(), name="customer_search"),
    path(
        "reservas/rapida/cliente-nuevo/",
        views.QuickCustomerCreateView.as_view(),
        name="quick_customer",
    ),
    path("reservas/<int:pk>/", views.ReservationDetailView.as_view(), name="detail"),
    path(
        "reservas/<int:pk>/pestana/<str:tab>/",
        views.ReservationTabView.as_view(),
        name="tab",
    ),
    path("reservas/<int:pk>/cabecera/", views.ReservationHeaderView.as_view(), name="header"),
    path("reservas/<int:pk>/fechas/", views.ChangeDatesView.as_view(), name="change_dates"),
    path(
        "reservas/<int:pk>/categoria/",
        views.ChangeCategoryView.as_view(),
        name="change_category",
    ),
    path("reservas/<int:pk>/vehiculo/", views.AssignVehicleView.as_view(), name="assign_vehicle"),
    path(
        "reservas/<int:pk>/vehiculo/liberar/",
        views.ReleaseVehicleView.as_view(),
        name="release_vehicle",
    ),
    path("reservas/<int:pk>/conductores/", views.DriverCreateView.as_view(), name="driver_create"),
    path(
        "reservas/<int:pk>/conductores/<int:driver_pk>/quitar/",
        views.DriverDeleteView.as_view(),
        name="driver_delete",
    ),
    path(
        "reservas/<int:pk>/estado/<str:to_status>/",
        views.ReservationTransitionView.as_view(),
        name="transition",
    ),
]
