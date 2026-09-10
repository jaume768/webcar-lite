from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("demo/", views.demo_login, name="demo_login"),
    path("oficina-activa/", views.set_active_office, name="set_active_office"),
    # Demostracion de componentes. Fuera cuando haya pantallas reales.
    path("ui-kit/", views.ui_kit, name="ui_kit"),
    path("ui-kit/vehiculos/", views.ui_kit_vehicle_search, name="ui_kit_vehicle_search"),
    path("ui-kit/modal/", views.ui_kit_modal, name="ui_kit_modal"),
    path("ui-kit/baja/", views.ui_kit_destructive, name="ui_kit_destructive"),
    path("ui-kit/error/<int:code>/", views.ui_kit_error, name="ui_kit_error"),
]
