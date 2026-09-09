from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("clientes/", views.CustomerListView.as_view(), name="customer_list"),
    path("clientes/nuevo/", views.CustomerCreateView.as_view(), name="customer_create"),
    path("clientes/<int:pk>/", views.CustomerDetailView.as_view(), name="customer_detail"),
    path("clientes/<int:pk>/editar/", views.CustomerUpdateView.as_view(), name="customer_update"),
    path(
        "clientes/<int:pk>/marca/",
        views.CustomerBlacklistView.as_view(),
        name="customer_blacklist",
    ),
    path(
        "clientes/<int:pk>/activar/",
        views.CustomerActivateView.as_view(),
        name="customer_activate",
    ),
    path(
        "clientes/<int:pk>/desactivar/",
        views.CustomerDeactivateView.as_view(),
        name="customer_deactivate",
    ),
    path(
        "clientes/<int:pk>/documentos/nuevo/",
        views.CustomerDocumentCreateView.as_view(),
        name="document_create",
    ),
    path(
        "documentos-de-cliente/<int:pk>/",
        views.CustomerDocumentDownloadView.as_view(),
        name="document_download",
    ),
    path(
        "documentos-de-cliente/<int:pk>/borrar/",
        views.CustomerDocumentDeleteView.as_view(),
        name="document_delete",
    ),
]
