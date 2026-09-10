from django.urls import path

from . import views

app_name = "contracts"

urlpatterns = [
    path("reservas/<int:pk>/contrato/", views.ContractCreateView.as_view(), name="create"),
    path(
        "reservas/<int:pk>/documentos/",
        views.DocumentsPanelView.as_view(),
        name="documents_panel",
    ),
    path(
        "reservas/<int:pk>/contrato/<int:contract_pk>/",
        views.ContractDownloadView.as_view(),
        name="download",
    ),
    path(
        "reservas/<int:pk>/danos/foto/<int:photo_pk>/",
        views.DamagePhotoDownloadView.as_view(),
        name="damage_photo",
    ),
]
