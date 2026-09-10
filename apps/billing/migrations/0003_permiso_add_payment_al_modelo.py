"""Mueve `add_payment` del ancla al modelo Payment, que ya existe.

Django genera `billing.add_payment` en cuanto hay modelo, asi que la fila del
ancla y la nueva tendrian el mismo `app_label.codename`: los roles apuntarian a
la vieja y quedaria una fila muerta. Se mueven las asignaciones y se borra.

`view_billing` se queda en el ancla: es un permiso de area, no de un modelo.
"""

from django.apps import apps as registro_de_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

CODENAME = "add_payment"


def _asegurar_permisos(apps):
    create_permissions(registro_de_apps.get_app_config("billing"), apps=apps, verbosity=0)


def _mover(apps, de_modelo, a_modelo):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Role = apps.get_model("accounts", "Role")

    origen = ContentType.objects.filter(app_label="billing", model=de_modelo).first()
    destino = ContentType.objects.filter(app_label="billing", model=a_modelo).first()
    if origen is None or destino is None:
        return

    viejo = Permission.objects.filter(content_type=origen, codename=CODENAME).first()
    if viejo is None:
        return

    nuevo, _creado = Permission.objects.get_or_create(
        content_type=destino, codename=CODENAME, defaults={"name": viejo.name}
    )
    for rol in Role.objects.filter(permissions=viejo):
        rol.permissions.add(nuevo)
        rol.permissions.remove(viejo)
    for grupo in nuevo.group_set.model.objects.filter(permissions=viejo):
        grupo.permissions.add(nuevo)
        grupo.permissions.remove(viejo)
    for usuario in nuevo.user_set.model.objects.filter(user_permissions=viejo):
        usuario.user_permissions.add(nuevo)
        usuario.user_permissions.remove(viejo)

    viejo.delete()


def adelante(apps, schema_editor):
    _asegurar_permisos(apps)
    _mover(apps, "billingpermissions", "payment")


def atras(apps, schema_editor):
    _mover(apps, "payment", "billingpermissions")


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0002_cobros"),
        ("accounts", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(adelante, atras)]
