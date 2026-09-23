from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("informes/", views.ReportsView.as_view(), name="reports"),
    path(
        "informes/exportar/facturas/",
        views.ExportView.as_view(documento="facturas"),
        name="export_invoices",
    ),
    path(
        "informes/exportar/cobros/",
        views.ExportView.as_view(documento="cobros"),
        name="export_payments",
    ),
]
