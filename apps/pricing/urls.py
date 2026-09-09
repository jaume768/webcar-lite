from django.urls import path

from . import views

app_name = "pricing"

urlpatterns = [
    # Extras
    path("extras/", views.ExtraListView.as_view(), name="extra_list"),
    path("extras/nuevo/", views.ExtraCreateView.as_view(), name="extra_create"),
    path("extras/<int:pk>/editar/", views.ExtraUpdateView.as_view(), name="extra_update"),
    path("extras/<int:pk>/activar/", views.ExtraActivateView.as_view(), name="extra_activate"),
    path(
        "extras/<int:pk>/desactivar/",
        views.ExtraDeactivateView.as_view(),
        name="extra_deactivate",
    ),
    # Temporadas
    path("temporadas/", views.SeasonListView.as_view(), name="season_list"),
    path("temporadas/nueva/", views.SeasonCreateView.as_view(), name="season_create"),
    path("temporadas/<int:pk>/editar/", views.SeasonUpdateView.as_view(), name="season_update"),
    path(
        "temporadas/<int:pk>/activar/",
        views.SeasonActivateView.as_view(),
        name="season_activate",
    ),
    path(
        "temporadas/<int:pk>/desactivar/",
        views.SeasonDeactivateView.as_view(),
        name="season_deactivate",
    ),
    # Tarifas
    path("tarifas/", views.RateListView.as_view(), name="rate_list"),
    path("tarifas/nueva/", views.RateCreateView.as_view(), name="rate_create"),
    path("tarifas/<int:pk>/editar/", views.RateUpdateView.as_view(), name="rate_update"),
    path("tarifas/<int:pk>/duplicar/", views.RateDuplicateView.as_view(), name="rate_duplicate"),
    path("tarifas/<int:pk>/activar/", views.RateActivateView.as_view(), name="rate_activate"),
    path(
        "tarifas/<int:pk>/desactivar/",
        views.RateDeactivateView.as_view(),
        name="rate_deactivate",
    ),
    # Aviso en vivo del editor de tramos
    path("tarifas/tramos/comprobar/", views.RateTierCheckView.as_view(), name="tier_check"),
    # Suplementos
    path("suplementos/", views.SupplementListView.as_view(), name="supplement_list"),
    path("suplementos/nuevo/", views.SupplementCreateView.as_view(), name="supplement_create"),
    path(
        "suplementos/<int:pk>/editar/",
        views.SupplementUpdateView.as_view(),
        name="supplement_update",
    ),
    path(
        "suplementos/<int:pk>/activar/",
        views.SupplementActivateView.as_view(),
        name="supplement_activate",
    ),
    path(
        "suplementos/<int:pk>/desactivar/",
        views.SupplementDeactivateView.as_view(),
        name="supplement_deactivate",
    ),
    # Descuentos
    path("descuentos/", views.DiscountListView.as_view(), name="discount_list"),
    path("descuentos/nuevo/", views.DiscountCreateView.as_view(), name="discount_create"),
    path(
        "descuentos/<int:pk>/editar/",
        views.DiscountUpdateView.as_view(),
        name="discount_update",
    ),
    path(
        "descuentos/<int:pk>/activar/",
        views.DiscountActivateView.as_view(),
        name="discount_activate",
    ),
    path(
        "descuentos/<int:pk>/desactivar/",
        views.DiscountDeactivateView.as_view(),
        name="discount_deactivate",
    ),
    # Herramientas
    path("simulador-de-precios/", views.PriceSimulatorView.as_view(), name="simulator"),
    path("tarifas/conflictos/", views.RateConflictListView.as_view(), name="rate_conflicts"),
]
