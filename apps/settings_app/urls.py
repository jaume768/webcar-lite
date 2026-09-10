from django.urls import path

from . import views

app_name = "settings_app"

urlpatterns = [
    path("configuracion/", views.SettingsView.as_view(), name="settings"),
    path("configuracion/condiciones/nueva/", views.TermsCreateView.as_view(), name="terms_create"),
    path(
        "configuracion/condiciones/<int:pk>/publicar/",
        views.TermsPublishView.as_view(),
        name="terms_publish",
    ),
]
