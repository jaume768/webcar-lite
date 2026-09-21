from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("correos/", views.EmailLogListView.as_view(), name="list"),
    path("correos/<int:pk>/reintentar/", views.EmailRetryView.as_view(), name="retry"),
]
