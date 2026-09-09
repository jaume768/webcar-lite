from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Habilita pg_trgm: lo necesita el indice de busqueda parcial de clientes.

    Sin la extension, un `icontains` sobre 50.000 clientes es un recorrido de
    tabla entero en cada tecla que se pulsa en el buscador.
    """

    dependencies = [("core", "0001_btree_gist")]

    operations = [
        TrigramExtension(),
    ]
