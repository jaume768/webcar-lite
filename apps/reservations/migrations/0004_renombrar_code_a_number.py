"""El localizador pasa a llamarse `number` y a ser una serie correlativa.

Se hace en su propia migracion porque un renombrado hay que decirselo a Django
explicitamente: si no, lo entiende como borrar una columna y crear otra, y eso
se llevaria por delante los localizadores ya emitidos.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("reservations", "0003_permisos_al_modelo_real"),
    ]

    operations = [
        migrations.RenameField(
            model_name="reservation",
            old_name="code",
            new_name="number",
        ),
    ]
