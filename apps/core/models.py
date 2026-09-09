"""Modelos base reutilizables. Aqui no hay dominio, solo comportamiento comun."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .middleware import get_current_user


class PhysicalDeleteNotAllowed(Exception):
    """Se ha intentado borrar de verdad un maestro que solo admite baja logica.

    No es un caso a capturar y seguir: si salta, es que hay codigo que no
    deberia existir. En la interfaz la opcion de borrar directamente no se
    ofrece.
    """


class TimeStampedModel(models.Model):
    """Marca de creacion y de ultima modificacion."""

    created_at = models.DateTimeField(_("creado el"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("actualizado el"), auto_now=True)

    class Meta:
        abstract = True


class ActivableQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)

    def delete(self):
        raise PhysicalDeleteNotAllowed(
            f"{self.model.__name__} no se borra en bloque: usa .update(is_active=False). "
            "Un maestro borrado deja huecos en reservas y facturas historicas."
        )


class ActivableManager(models.Manager.from_queryset(ActivableQuerySet)):
    """Manager por defecto de los maestros. No filtra: hay que pedir .active()."""


class ActivableModel(models.Model):
    """Baja logica. En maestros no se borra nunca: un vehiculo dado de baja
    tiene que seguir apareciendo en las reservas historicas."""

    is_active = models.BooleanField(_("activo"), default=True, db_index=True)

    objects = ActivableManager()

    class Meta:
        abstract = True

    def deactivate(self, *, save: bool = True) -> None:
        self.is_active = False
        if save:
            self.save(update_fields=["is_active"])

    def activate(self, *, save: bool = True) -> None:
        self.is_active = True
        if save:
            self.save(update_fields=["is_active"])

    def delete(self, *args, **kwargs):
        """Los maestros no se borran.

        Una reserva de hace dos anos apunta a esta fila: si desaparece, el
        historico deja de poder leerse. Se da de baja con `deactivate()`.
        """
        raise PhysicalDeleteNotAllowed(
            f"{type(self).__name__} no se borra: usa deactivate(). "
            f"Sigue haciendo falta en el historico ({self})."
        )


class UserStampedModel(models.Model):
    """Quien creo y quien modifico por ultima vez.

    Se rellena solo con el usuario de la request en curso (ver `middleware`).
    Sin usuario en contexto (comando, tarea, migracion) los campos quedan a
    NULL en lugar de reventar.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("creado por"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("modificado por"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = get_current_user()
        if user is not None:
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user

            # Un save(update_fields=[...]) que no incluya estos campos los
            # dejaria sin escribir en silencio.
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                update_fields = set(update_fields)
                if update_fields:
                    update_fields.add("updated_by")
                    if self._state.adding:
                        update_fields.add("created_by")
                    kwargs["update_fields"] = update_fields

        super().save(*args, **kwargs)
