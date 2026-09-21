from django.urls import path

from . import views

app_name = "compliance"

urlpatterns = [
    path("ses-hospedajes/", views.SesListView.as_view(), name="ses_list"),
    path("ses-hospedajes/<int:pk>/", views.SesDetailView.as_view(), name="ses_detail"),
    path("ses-hospedajes/<int:pk>/xml/", views.SesXmlView.as_view(), name="ses_xml"),
    path(
        "ses-hospedajes/<int:pk>/<str:accion>/",
        views.SesActionView.as_view(),
        name="ses_action",
    ),
]
