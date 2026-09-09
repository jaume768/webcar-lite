"""Almacenes de ficheros del proyecto."""

from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """Ficheros sin URL, guardados fuera de MEDIA_ROOT.

    `FileSystemStorage` con `base_url=None` **no** deja el fichero sin URL: cae
    en `MEDIA_URL` sin avisar. Aqui se corta a proposito, para que un descuido
    en una plantilla (`{{ documento.file.url }}`) reviente en desarrollo en vez
    de publicar el escaneo de un DNI.

    La unica forma de leer estos ficheros es una vista que compruebe permisos,
    como `customers:document_download`.
    """

    def url(self, name):
        raise ValueError(
            "Este fichero es privado y no tiene URL. Sirvelo con una vista que "
            "compruebe permisos (ver customers.views.CustomerDocumentDownloadView)."
        )
