from django.urls import path

from . import views

app_name = "auditlog"

urlpatterns = [
    path("auditoria/", views.AuditLogListView.as_view(), name="list"),
]
