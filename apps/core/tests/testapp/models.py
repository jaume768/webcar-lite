"""Modelos concretos de usar y tirar para ejercitar los mixins."""

from django.db import models

from apps.accounts.scoping import OfficeScopedModel
from apps.core.models import ActivableModel, TimeStampedModel, UserStampedModel


class Widget(TimeStampedModel, ActivableModel, UserStampedModel):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = "core_testapp"

    def __str__(self):
        return self.name


class ScopedWidget(TimeStampedModel, OfficeScopedModel):
    """Modelo con oficina, para probar el aislamiento de datos."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "core_testapp"

    def __str__(self):
        return self.name
