"""Fixtures de los tests de clientes."""

import pytest


@pytest.fixture(autouse=True)
def almacen_privado_temporal(settings, tmp_path):
    """Los documentos de prueba no tocan el almacen real.

    Es el mismo backend que en produccion (sin URL publica), pero apuntando a un
    directorio que pytest borra al terminar.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "private": {
            "BACKEND": "apps.core.storage.PrivateFileSystemStorage",
            "OPTIONS": {"location": str(tmp_path / "privado")},
        },
    }
    return tmp_path / "privado"
