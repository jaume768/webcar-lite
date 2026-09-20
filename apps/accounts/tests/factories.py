"""Fabricas para los tests. Usuarios, roles y oficinas en una linea."""

import factory
from django.contrib.auth.models import Permission

from apps.accounts.models import Role, User
from apps.offices.models import Office

CONTRASENA = "contrasena-de-prueba-123"


class OfficeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Office
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"oficina-{n}")
    name = factory.Sequence(lambda n: f"Oficina {n}")
    city = "Valencia"
    province = "Valencia"
    postal_code = "07001"


class RoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Role
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    code = factory.Sequence(lambda n: f"rol-{n}")
    name = factory.Sequence(lambda n: f"Rol {n}")

    @factory.post_generation
    def permissions(self, create, extracted, **kwargs):
        """Acepta permisos como "app_label.codename"."""
        if not create or not extracted:
            return
        for codigo in extracted:
            app_label, _, codename = codigo.partition(".")
            self.permissions.add(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"usuario{n}@ejemplo.es")
    first_name = "Ana"
    last_name = factory.Sequence(lambda n: f"Apellido{n}")
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_password(extracted or CONTRASENA)
        self.save(update_fields=["password"])

    @factory.post_generation
    def offices(self, create, extracted, **kwargs):
        if create and extracted:
            self.offices.set(extracted)
