from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Habilita btree_gist: lo necesitan las ExclusionConstraint sobre tstzrange."""

    initial = True

    dependencies = []

    operations = [
        BtreeGistExtension(),
    ]
