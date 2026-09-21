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
    path("politicas/", views.PolicyListView.as_view(), name="policy_list"),
    path("politicas/nueva/", views.PolicyCreateView.as_view(), name="policy_create"),
    path("politicas/<int:pk>/editar/", views.PolicyUpdateView.as_view(), name="policy_update"),
    path(
        "politicas/<int:pk>/activar/",
        views.PolicyActivateView.as_view(),
        name="policy_activate",
    ),
    path(
        "politicas/<int:pk>/desactivar/",
        views.PolicyDeactivateView.as_view(),
        name="policy_deactivate",
    ),
]
