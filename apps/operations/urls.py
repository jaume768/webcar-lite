from django.urls import path

from . import views

app_name = "operations"

urlpatterns = [
    path("reservas/<int:pk>/entrega/", views.CheckInView.as_view(), name="check_in"),
    path("reservas/<int:pk>/devolucion/", views.CheckOutView.as_view(), name="check_out"),
    path("reservas/<int:pk>/danos/nuevo/", views.DamageCreateView.as_view(), name="damage_create"),
]
