from django.urls import path

from . import views

app_name = "operations"

urlpatterns = [
    path("reservas/<int:pk>/entrega/", views.CheckInView.as_view(), name="check_in"),
    path(
        "reservas/<int:pk>/entrega/firma/",
        views.CheckInSignatureView.as_view(),
        name="check_in_signature",
    ),
    path("reservas/<int:pk>/devolucion/", views.CheckOutView.as_view(), name="check_out"),
    path("reservas/<int:pk>/danos/nuevo/", views.DamageCreateView.as_view(), name="damage_create"),
    path("multas/", views.TrafficFineListView.as_view(), name="fine_list"),
    path("multas/nueva/", views.TrafficFineCreateView.as_view(), name="fine_create"),
    path("multas/<int:pk>/", views.TrafficFineDetailView.as_view(), name="fine_detail"),
    path("multas/<int:pk>/editar/", views.TrafficFineUpdateView.as_view(), name="fine_update"),
    path(
        "multas/<int:pk>/<str:accion>/",
        views.TrafficFineActionView.as_view(),
        name="fine_action",
    ),
]
