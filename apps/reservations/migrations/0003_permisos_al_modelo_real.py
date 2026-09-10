"""Traslada los permisos del ancla al modelo Reservation, ya que existe.

Hasta ahora `change_reservation_price`, `cancel_reservation` y
`delete_reservation` colgaban de un modelo sin tabla que solo servia para dar
un content type a la app. Con el modelo real creado, Django genera esos mismos
codigos sobre `reservations.reservation`, y quedarian dos filas con el mismo
`app_label.codename`: los roles apuntarian a la vieja y `sync_roles` no sabria
cual elegir.

Esta migracion mueve las asignaciones (roles, grupos y usuarios) a la fila
nueva y borra la vieja. Como el codigo no cambia, ni las vistas ni los roles se
enteran: `user.has_perm("reservations.cancel_reservation")` sigue respondiendo
lo mismo antes y despues.
"""

from django.apps import apps as registro_de_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

CODIGOS = ["change_reservation_price", "cancel_reservation", "delete_reservation"]

ANCLA = "reservationpermissions"
MODELO = "reservation"


def _asegurar_permisos_del_modelo(apps):
    """Crea ya los permisos de `reservations`, sin esperar a post_migrate.

    Django los crea al terminar **todas** las migraciones, asi que dentro de una
    RunPython todavia no existen ni el content type ni los permisos del modelo
    nuevo. Sin esto, la migracion no encontraria el destino y no haria nada.
    """
    config = registro_de_apps.get_app_config("reservations")
    create_permissions(config, apps=apps, verbosity=0)


def _mover(apps, de_modelo, a_modelo):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Role = apps.get_model("accounts", "Role")

    origen = ContentType.objects.filter(app_label="reservations", model=de_modelo).first()
    destino = ContentType.objects.filter(app_label="reservations", model=a_modelo).first()
    if origen is None or destino is None:
        return

    for codename in CODIGOS:
        viejo = Permission.objects.filter(content_type=origen, codename=codename).first()
        if viejo is None:
            continue

        nuevo, _creado = Permission.objects.get_or_create(
            content_type=destino,
            codename=codename,
            defaults={"name": viejo.name},
        )

        # Quien tuviera el permiso lo conserva.
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

    # El content type del ancla ya no representa nada.
    origen.delete()


def adelante(apps, schema_editor):
    _asegurar_permisos_del_modelo(apps)
    _mover(apps, ANCLA, MODELO)


def atras(apps, schema_editor):
    """Vuelta atras: los permisos regresan al ancla, que 0002 recrea al revertirse."""
    _mover(apps, MODELO, ANCLA)


class Migration(migrations.Migration):
    dependencies = [
        ("reservations", "0002_reserva_minima"),
        ("accounts", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(adelante, atras),
    ]
