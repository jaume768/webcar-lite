from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path(
        "reservas/<int:pk>/cobros/nuevo/",
        views.PaymentCreateView.as_view(),
        name="payment_create",
    ),
    path(
        "reservas/<int:pk>/cobros/<int:payment_pk>/devolver/",
        views.PaymentRefundView.as_view(),
        name="payment_refund",
    ),
    path("caja/", views.CashRegisterView.as_view(), name="cash_register"),
]
