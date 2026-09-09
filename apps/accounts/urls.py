from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("salir/", views.LogoutView.as_view(), name="logout"),
    path("contrasena/cambiar/", views.PasswordChangeView.as_view(), name="password_change"),
    path(
        "contrasena/cambiada/",
        views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path("contrasena/recuperar/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "contrasena/recuperar/enviado/",
        views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "contrasena/nueva/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "contrasena/nueva/hecho/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("usuarios/", views.UserListView.as_view(), name="user_list"),
    path("usuarios/nuevo/", views.UserCreateView.as_view(), name="user_create"),
    path("usuarios/<int:pk>/editar/", views.UserUpdateView.as_view(), name="user_update"),
    path("usuarios/<int:pk>/desactivar/", views.user_deactivate, name="user_deactivate"),
    path("usuarios/<int:pk>/activar/", views.user_activate, name="user_activate"),
]
