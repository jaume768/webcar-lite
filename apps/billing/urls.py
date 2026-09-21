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
    path(
        "reservas/<int:pk>/facturar/",
        views.InvoiceIssueView.as_view(),
        name="invoice_issue",
    ),
    path("caja/", views.CashRegisterView.as_view(), name="cash_register"),
    path("facturacion/pendiente/", views.ToInvoiceListView.as_view(), name="to_invoice"),
    path("facturacion/facturas/", views.InvoiceListView.as_view(), name="invoice_list"),
    path(
        "facturacion/facturas/<int:pk>/",
        views.InvoiceDetailView.as_view(),
        name="invoice_detail",
    ),
    path(
        "facturacion/facturas/<int:pk>/pdf/",
        views.InvoicePdfView.as_view(),
        name="invoice_pdf",
    ),
    path(
        "facturacion/facturas/<int:pk>/rectificar/",
        views.InvoiceRectifyView.as_view(),
        name="invoice_rectify",
    ),
    path("facturacion/cobros/", views.PaymentListView.as_view(), name="payment_list"),
    path("facturacion/series/", views.InvoiceSeriesListView.as_view(), name="series_list"),
    path(
        "facturacion/series/nueva/",
        views.InvoiceSeriesCreateView.as_view(),
        name="series_create",
    ),
    path(
        "facturacion/series/<int:pk>/editar/",
        views.InvoiceSeriesUpdateView.as_view(),
        name="series_update",
    ),
    path(
        "facturacion/series/<int:pk>/activar/",
        views.InvoiceSeriesActivateView.as_view(),
        name="series_activate",
    ),
    path(
        "facturacion/series/<int:pk>/desactivar/",
        views.InvoiceSeriesDeactivateView.as_view(),
        name="series_deactivate",
    ),
]
