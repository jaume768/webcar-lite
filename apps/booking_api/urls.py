from django.urls import path

from . import views

app_name = "booking_api"

urlpatterns = [
    path("api/v1/offices/", views.OfficesView.as_view(), name="offices"),
    path("api/v1/extras/", views.ExtrasView.as_view(), name="extras"),
    path(
        "api/v1/payment-providers/",
        views.PaymentProvidersView.as_view(),
        name="payment_providers",
    ),
    path("api/v1/availability/", views.AvailabilityView.as_view(), name="availability"),
    path("api/v1/reservations/", views.ReservationCreateView.as_view(), name="reservation_create"),
    path(
        "api/v1/reservations/<str:number>/",
        views.ReservationDetailView.as_view(),
        name="reservation_detail",
    ),
    path(
        "api/v1/reservations/<str:number>/cancel/",
        views.ReservationCancelView.as_view(),
        name="reservation_cancel",
    ),
    path(
        "api/v1/reservations/<str:number>/payment-link/",
        views.PaymentLinkView.as_view(),
        name="reservation_payment_link",
    ),
]
