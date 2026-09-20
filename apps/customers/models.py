"""Clientes y conductores.

El cliente es de la empresa, no de una oficina: quien alquila en una oficina en mayo
puede alquilar en otra en agosto, y hacerle repetir el alta seria un
disparate en mostrador. Se guarda la oficina de alta como dato (`office`), que
sirve para filtrar y para saber quien lo capto, pero **no** recorta quien puede
leerlo.
"""

from django.contrib.postgres.indexes import GinIndex
from django.core.files.storage import storages
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel, UserStampedModel
from apps.core.text import normalizar, normalizar_documento

from .validators import validar_documento


def documentos_privados():
    """Almacen sin URL publica. Ver `settings.STORAGES['private']`."""
    return storages["private"]


def ruta_documento(instance, filename: str) -> str:
    """Los ficheros van agrupados por cliente, con el nombre original al final."""
    return f"clientes/{instance.customer_id}/{filename}"


class DocumentType(models.TextChoices):
    DNI = "dni", _("DNI")
    NIE = "nie", _("NIE")
    PASSPORT = "passport", _("Pasaporte")


class CustomerDocumentKind(models.TextChoices):
    ID_FRONT = "id_front", _("Documento de identidad (anverso)")
    ID_BACK = "id_back", _("Documento de identidad (reverso)")
    LICENCE_FRONT = "licence_front", _("Carnet de conducir (anverso)")
    LICENCE_BACK = "licence_back", _("Carnet de conducir (reverso)")
    OTHER = "other", _("Otro")


class CustomerQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)

    def search(self, termino: str):
        """Busqueda parcial por nombre, apellidos, documento, telefono o correo.

        Ataca `search_text`, que ya viene en minusculas y sin acentos, con el
        indice trigram (GIN) detras: "gonzal" encuentra "Gonzalez" sin recorrer
        la tabla entera, y da igual si quien busca escribe la tilde o no.
        """
        normalizado = normalizar(termino)
        if not normalizado:
            return self
        return self.filter(search_text__contains=normalizado)

    def delete(self):
        from apps.core.models import PhysicalDeleteNotAllowed

        raise PhysicalDeleteNotAllowed(
            "Customer no se borra en bloque: usa .update(is_active=False). "
            "Un cliente borrado deja reservas y facturas historicas sin titular."
        )


class CustomerManager(models.Manager.from_queryset(CustomerQuerySet)):
    """Manager por defecto. No filtra solo: hay que pedir `active()`."""


class Customer(TimeStampedModel, ActivableModel, UserStampedModel):
    """Titular de una reserva.

    Guarda tambien los datos de conduccion porque en la mayoria de alquileres el
    titular es el conductor principal. Los conductores adicionales llegaran con
    `reservations.ReservationDriver`.
    """

    # --- Identidad ---------------------------------------------------------
    first_name = models.CharField(_("nombre"), max_length=80)
    last_name = models.CharField(_("apellidos"), max_length=120)
    birth_date = models.DateField(
        _("fecha de nacimiento"),
        null=True,
        blank=True,
        help_text=_("Necesaria para el suplemento de conductor joven."),
    )
    nationality = models.CharField(_("nacionalidad"), max_length=2, default="ES")

    document_type = models.CharField(
        _("tipo de documento"),
        max_length=20,
        choices=DocumentType.choices,
        default=DocumentType.DNI,
    )
    document_number = models.CharField(_("numero de documento"), max_length=20)
    document_expiry = models.DateField(_("caducidad del documento"), null=True, blank=True)

    # --- Contacto ----------------------------------------------------------
    email = models.EmailField(_("correo electronico"), blank=True)
    phone = models.CharField(_("telefono"), max_length=20, blank=True)
    phone_alt = models.CharField(_("telefono alternativo"), max_length=20, blank=True)

    address = models.CharField(_("direccion"), max_length=200, blank=True)
    city = models.CharField(_("localidad"), max_length=100, blank=True)
    province = models.CharField(_("provincia"), max_length=100, blank=True)
    postal_code = models.CharField(_("codigo postal"), max_length=10, blank=True)
    country = models.CharField(_("pais"), max_length=2, default="ES")

    # --- Conduccion --------------------------------------------------------
    licence_number = models.CharField(_("numero de carnet"), max_length=30, blank=True)
    licence_country = models.CharField(_("pais del carnet"), max_length=2, default="ES")
    licence_issued_on = models.DateField(
        _("fecha de expedicion"),
        null=True,
        blank=True,
        help_text=_("De aqui sale la antiguedad de carnet que exigen las tarifas."),
    )
    licence_expiry = models.DateField(_("caducidad del carnet"), null=True, blank=True)

    # --- Gestion interna ---------------------------------------------------
    office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina de alta"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="customers",
        help_text=_("Donde se dio de alta. No limita quien puede atenderle."),
    )
    notes = models.TextField(
        _("notas internas"),
        blank=True,
        help_text=_("No se imprimen en el contrato ni las ve el cliente."),
    )
    is_blacklisted = models.BooleanField(
        _("cliente conflictivo"),
        default=False,
        db_index=True,
        help_text=_("Se avisa al crear una reserva con este cliente."),
    )
    blacklist_reason = models.TextField(
        _("motivo"),
        blank=True,
        help_text=_("Que paso. Lo lee quien esta en el mostrador antes de alquilar."),
    )

    #: Columna desnormalizada para el buscador: minusculas, sin acentos y con
    #: todo lo buscable junto. La rellena `save()`; nadie la escribe a mano.
    search_text = models.TextField(_("texto de busqueda"), blank=True, editable=False)

    objects = CustomerManager()

    class Meta:
        verbose_name = _("cliente")
        verbose_name_plural = _("clientes")
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "document_number"],
                name="customers_documento_unico",
                violation_error_message=_("Ya hay un cliente con ese documento."),
            )
        ]
        indexes = [
            # GIN + trigram: busqueda parcial rapida ("gonzal" -> "gonzalez")
            # incluso con decenas de miles de clientes.
            GinIndex(
                name="customers_busqueda_trgm",
                fields=["search_text"],
                opclasses=["gin_trgm_ops"],
            ),
            models.Index(fields=["last_name", "first_name"], name="customers_nombre"),
        ]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.document_number = normalizar_documento(self.document_number)
        self.search_text = self.build_search_text()
        if (update_fields := kwargs.get("update_fields")) is not None:
            # Un save parcial que toque un campo buscable tiene que reescribir
            # tambien la columna del buscador, o el indice se queda antiguo.
            kwargs["update_fields"] = {*update_fields, "search_text", "document_number"}
        super().save(*args, **kwargs)

    def build_search_text(self) -> str:
        partes = [
            self.first_name,
            self.last_name,
            self.document_number,
            self.email,
            self.phone,
            self.phone_alt,
            self.licence_number,
        ]
        return normalizar(" ".join(parte for parte in partes if parte))

    def clean(self):
        super().clean()
        if self.document_number:
            self.document_number = validar_documento(self.document_type, self.document_number)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self) -> int | None:
        """Edad cumplida hoy. None si no hay fecha de nacimiento."""
        if not self.birth_date:
            return None
        hoy = timezone.localdate()
        cumplido = (hoy.month, hoy.day) >= (self.birth_date.month, self.birth_date.day)
        return hoy.year - self.birth_date.year - (0 if cumplido else 1)

    @property
    def licence_years(self) -> int | None:
        """Anos de antiguedad del carnet. Los pide la tarifa de conductor joven."""
        if not self.licence_issued_on:
            return None
        hoy = timezone.localdate()
        cumplido = (hoy.month, hoy.day) >= (
            self.licence_issued_on.month,
            self.licence_issued_on.day,
        )
        return hoy.year - self.licence_issued_on.year - (0 if cumplido else 1)


class CustomerDocument(TimeStampedModel, UserStampedModel):
    """Copia escaneada del DNI o del carnet.

    Se guarda en un almacen privado, fuera de MEDIA_ROOT: no tiene URL y el
    servidor de estaticos no lo sirve. Se descarga por
    `customers:document_download`, que comprueba permisos antes de entregarlo.
    Es dato personal: una URL adivinable seria una brecha, no un descuido.
    """

    customer = models.ForeignKey(
        Customer,
        verbose_name=_("cliente"),
        on_delete=models.CASCADE,
        related_name="documents",
    )
    kind = models.CharField(
        _("tipo"),
        max_length=20,
        choices=CustomerDocumentKind.choices,
        default=CustomerDocumentKind.ID_FRONT,
    )
    file = models.FileField(
        _("fichero"),
        upload_to=ruta_documento,
        storage=documentos_privados,
    )
    original_name = models.CharField(_("nombre original"), max_length=200, blank=True)
    notes = models.CharField(_("descripcion"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("documento de cliente")
        verbose_name_plural = _("documentos de cliente")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer}: {self.get_kind_display()}"

    def delete(self, *args, **kwargs):
        """Al quitar el documento se borra tambien el fichero.

        Un escaneo de un DNI que sobrevive a su propia ficha es justo lo que no
        se puede quedar en el disco.
        """
        fichero = self.file
        super().delete(*args, **kwargs)
        fichero.delete(save=False)
