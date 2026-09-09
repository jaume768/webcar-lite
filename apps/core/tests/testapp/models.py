"""Modelo concreto de usar y tirar para ejercitar los mixins de core."""

from django.db import models

from apps.core.models import ActivableModel, TimeStampedModel, UserStampedModel


class Widget(TimeStampedModel, ActivableModel, UserStampedModel):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = "core_testapp"

    def __str__(self):
        return self.name
