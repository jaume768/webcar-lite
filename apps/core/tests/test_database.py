import pytest
from django.db import connection


@pytest.mark.django_db
def test_la_base_de_datos_es_postgres():
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_extension_btree_gist_instalada():
    """La migracion core.0001 debe dejar btree_gist disponible en la BD de tests."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
        assert cursor.fetchone() is not None
