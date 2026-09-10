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
        "reservas/<int:pk>/vehiculo/",
        views.AssignVehicleView.as_view(),
        name="assign_vehicle",
    ),
    path(
        "reservas/<int:pk>/estado/<str:to_status>/",
        views.ReservationTransitionView.as_view(),
        name="transition",
    ),
]
