"""Cobros y facturas.

Un cobro es un hecho: ocurrio, con una fecha, un importe y un medio de pago.
Por eso no se edita ni se borra nunca: si esta mal, se corrige con un apunte
contrario, igual que en contabilidad. Una fila borrada aqui es dinero que
desaparece del arqueo.

Las facturas siguen la misma regla con mas motivo todavia: una factura emitida
no se toca, se corrige con una rectificativa.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActivableModel, TimeStampedModel


class PaymentImmutable(Exception):
    """Se ha intentado editar o borrar un cobro ya registrado."""


class BillingPermissions(models.Model):  # noqa: DJ008 - sin filas: no hay nada que representar
    """Ancla de los permisos de area, los que no cuelgan de ningun modelo."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("permisos de facturacion")
        permissions = [
            ("view_billing", _("Puede consultar facturacion y cobros")),
        ]


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("Efectivo")
    CARD = "card", _("Tarjeta")
    TRANSFER = "transfer", _("Transferencia")
    PAYPAL = "paypal", _("PayPal")
    DATAPHONE = "dataphone", _("Datafono")
    OTHER = "other", _("Otros")


class PaymentType(models.TextChoices):
    ADVANCE = "advance", _("Anticipo")
    PAYMENT = "payment", _("Pago")
    DEPOSIT = "deposit", _("Fianza")
    DEPOSIT_RETURN = "deposit_return", _("Devolucion de fianza")
    ADDITIONAL_CHARGE = "additional_charge", _("Cargo adicional")
    REFUND = "refund", _("Reembolso")


#: Tipos que mueven lo cobrado del alquiler.
RENTAL_TYPES = (
    PaymentType.ADVANCE,
    PaymentType.PAYMENT,
    PaymentType.ADDITIONAL_CHARGE,
    PaymentType.REFUND,
)

#: La fianza no es un cobro: es dinero retenido que se devuelve. Va por su
#: cuenta y no cuenta como pagado del alquiler.
DEPOSIT_TYPES = (PaymentType.DEPOSIT, PaymentType.DEPOSIT_RETURN)

#: Salidas de dinero: se guardan en negativo, nunca borrando el cobro original.
OUTGOING_TYPES = (PaymentType.REFUND, PaymentType.DEPOSIT_RETURN)

INCOMING_TYPES = tuple(tipo for tipo in PaymentType.values if tipo not in OUTGOING_TYPES)


class PaymentQuerySet(models.QuerySet):
    def rental(self):
        """Los que mueven el saldo del alquiler."""
        return self.filter(payment_type__in=RENTAL_TYPES)

    def deposits(self):
        """Fianza retenida y devuelta."""
        return self.filter(payment_type__in=DEPOSIT_TYPES)

    def cash(self):
        return self.filter(method=PaymentMethod.CASH)

    def on_day(self, day, office=None):
        """Cobros de un dia natural, en hora local."""
        inicio = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
        consulta = self.filter(paid_at__gte=inicio, paid_at__lt=inicio + timezone.timedelta(days=1))
        return consulta.filter(office=office) if office is not None else consulta

    def total(self) -> Decimal:
        return self.aggregate(suma=models.Sum("amount"))["suma"] or Decimal("0.00")

    def for_user(self, user):
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(office__in=user.offices.all())


class Payment(TimeStampedModel):
    """Un movimiento de dinero de una reserva.

    `created_by` va explicito y no por `UserStampedModel`: aqui no hay
    `updated_by` que valga, porque un cobro no se modifica nunca.
    """

    reservation = models.ForeignKey(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        _("importe"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Negativo en devoluciones y reembolsos."),
    )
    method = models.CharField(_("medio de pago"), max_length=20, choices=PaymentMethod.choices)
    payment_type = models.CharField(
        _("concepto"), max_length=20, choices=PaymentType.choices, default=PaymentType.PAYMENT
    )

    paid_at = models.DateTimeField(_("fecha del cobro"), default=timezone.now, db_index=True)
    reference = models.CharField(
        _("referencia"),
        max_length=60,
        blank=True,
        default="",
        help_text=_("Numero de operacion del datafono, del banco o del recibo."),
    )
    office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina"),
        on_delete=models.PROTECT,
        related_name="payments",
        help_text=_("Donde se cobro. Es la oficina que cuadra caja."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("registrado por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments_registered",
    )
    notes = models.TextField(_("notas"), blank=True, default="")

    objects = PaymentQuerySet.as_manager()

    class Meta:
        verbose_name = _("cobro")
        verbose_name_plural = _("cobros")
        ordering = ["-paid_at", "-id"]
        permissions = [
            ("allow_overpayment", _("Puede cobrar por encima del pendiente")),
        ]
        constraints = [
            CheckConstraint(
                condition=~Q(amount=0),
                name="billing_importe_distinto_de_cero",
            ),
            # El signo lo manda el concepto: una devolucion en positivo seria
            # dinero inventado y un cobro en negativo, dinero perdido.
            CheckConstraint(
                condition=(
                    Q(payment_type__in=OUTGOING_TYPES, amount__lt=0)
                    | Q(payment_type__in=INCOMING_TYPES, amount__gt=0)
                ),
                name="billing_signo_coherente_con_el_concepto",
            ),
        ]
        indexes = [
            models.Index(fields=["office", "paid_at"], name="billing_pago_oficina_fecha"),
            models.Index(fields=["reservation", "paid_at"], name="billing_pago_reserva"),
        ]

    def __str__(self):
        return f"{self.get_payment_type_display()} {self.amount} EUR"

    def save(self, *args, **kwargs):
        """Solo alta. Un cobro registrado no se toca.

        Corregir un importe editando la fila dejaria el arqueo de ayer
        diciendo una cosa distinta de la que dijo ayer. Se corrige con un
        apunte contrario.
        """
        if self.pk is not None:
            raise PaymentImmutable(
                f"El cobro {self.pk} ya esta registrado: para corregirlo, "
                "registra un apunte contrario."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PaymentImmutable(
            f"El cobro {self.pk} no se borra: registra un reembolso por el mismo importe."
        )

    @property
    def is_deposit(self) -> bool:
        return self.payment_type in DEPOSIT_TYPES

    @property
    def is_outgoing(self) -> bool:
        return self.amount < 0


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------


class InvoiceImmutable(Exception):
    """Se ha intentado editar o borrar una factura ya emitida."""


class InvoiceKind(models.TextChoices):
    ORDINARY = "ordinary", _("Ordinaria")
    RECTIFYING = "rectifying", _("Rectificativa")


#: Tipo de factura en el vocabulario de Verifactu. F1 es la factura completa;
#: R4 la rectificativa "resto de causas", la que corresponde a anular una
#: factura del alquiler para volver a emitirla bien.
TIPO_VERIFACTU = {
    InvoiceKind.ORDINARY: "F1",
    InvoiceKind.RECTIFYING: "R4",
}

FORMATO_DE_SERIE_POR_DEFECTO = "F{year}-{sequence:05d}"


class InvoiceSeries(TimeStampedModel, ActivableModel):
    """Serie de numeracion de facturas.

    La numeracion es correlativa y sin huecos dentro de cada serie (y de cada
    ano, si el formato lleva `{year}`). Las rectificativas van en una serie
    propia: la ley pide poder distinguirlas a simple vista.
    """

    code = models.SlugField(
        _("codigo"),
        max_length=20,
        unique=True,
        error_messages={"unique": _("Ya existe una serie con ese codigo.")},
    )
    name = models.CharField(_("nombre"), max_length=80)
    kind = models.CharField(
        _("tipo"), max_length=20, choices=InvoiceKind.choices, default=InvoiceKind.ORDINARY
    )
    number_format = models.CharField(
        _("formato del numero"),
        max_length=40,
        default=FORMATO_DE_SERIE_POR_DEFECTO,
        help_text=_(
            "Usa {sequence} para el correlativo y, si quieres que se reinicie cada ano, "
            "{year}. Ejemplo: F{year}-{sequence:05d} da F2026-00001."
        ),
    )
    is_default = models.BooleanField(
        _("serie por defecto"),
        default=False,
        help_text=_("La que se propone al emitir. Solo una por tipo."),
    )
    notes = models.TextField(_("notas"), blank=True, default="")

    class Meta:
        verbose_name = _("serie de facturacion")
        verbose_name_plural = _("series de facturacion")
        ordering = ["kind", "code"]
        # Una serie no se borra nunca: sus facturas la siguen necesitando.
        default_permissions = ("view", "add", "change")
        constraints = [
            models.UniqueConstraint(
                fields=["kind"],
                condition=Q(is_default=True),
                name="billing_una_serie_por_defecto_por_tipo",
            ),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def uses_year(self) -> bool:
        return "{year}" in self.number_format

    def scope_for(self, when) -> str:
        """Ambito del contador: el ano si el formato lo lleva, si no uno solo."""
        return str(timezone.localtime(when).year) if self.uses_year else "global"

    def format_number(self, *, scope: str, sequence: int) -> str:
        return self.number_format.format(year=scope, sequence=sequence)

    @property
    def next_number(self) -> str:
        """Numero que llevaria la proxima factura, para ensenarlo en el listado.

        Solo informa: el numero de verdad se asigna al emitir, con la serie
        bloqueada. Recorre `counters.all()` para aprovechar el prefetch.
        """
        ambito = self.scope_for(timezone.now())
        ultimo = next((c.last_number for c in self.counters.all() if c.scope == ambito), 0)
        return self.format_number(scope=ambito, sequence=ultimo + 1)


class InvoiceSeriesCounter(models.Model):
    """Ultimo numero usado de una serie en un ambito (un ano, o "global").

    Mismo razonamiento que el contador de reservas: una secuencia de Postgres
    salta numeros al deshacer una transaccion, y una serie de facturas no puede
    tener huecos. Se incrementa con la fila de la serie bloqueada.
    """

    series = models.ForeignKey(
        InvoiceSeries, verbose_name=_("serie"), on_delete=models.PROTECT, related_name="counters"
    )
    scope = models.CharField(_("ambito"), max_length=20)
    last_number = models.PositiveIntegerField(_("ultimo numero"), default=0)

    class Meta:
        verbose_name = _("contador de serie")
        verbose_name_plural = _("contadores de serie")
        constraints = [
            models.UniqueConstraint(
                fields=["series", "scope"], name="billing_un_contador_por_serie_y_ambito"
            ),
        ]

    def __str__(self):
        return f"{self.series.code} {self.scope}: {self.last_number}"


class InvoiceQuerySet(models.QuerySet):
    def for_user(self, user):
        if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(office__in=user.offices.all())

    def ordinary(self):
        return self.filter(kind=InvoiceKind.ORDINARY)

    def in_force(self):
        """Ordinarias que ninguna rectificativa ha anulado."""
        return self.ordinary().filter(rectifications__isnull=True)


class Invoice(TimeStampedModel):
    """Factura emitida. Inmutable desde que existe.

    Copia los datos fiscales de la empresa y del cliente en el momento de
    emitirse: si el cliente cambia de direccion manana, la factura de ayer
    sigue diciendo lo que dijo. Los totales son la suma de sus lineas.

    Lleva desde el principio el encadenado de Verifactu (`hash_anterior`,
    `hash_actual`, `qr_data`, `fecha_registro`), aunque el envio a la AEAT
    llegue mas adelante: una cadena no se puede reconstruir hacia atras.
    """

    series = models.ForeignKey(
        InvoiceSeries, verbose_name=_("serie"), on_delete=models.PROTECT, related_name="invoices"
    )
    scope = models.CharField(_("ambito de numeracion"), max_length=20)
    sequence = models.PositiveIntegerField(_("correlativo"))
    number = models.CharField(_("numero"), max_length=40, unique=True)
    kind = models.CharField(_("tipo"), max_length=20, choices=InvoiceKind.choices)
    issued_at = models.DateTimeField(_("fecha de expedicion"), db_index=True)

    reservation = models.ForeignKey(
        "reservations.Reservation",
        verbose_name=_("reserva"),
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    office = models.ForeignKey(
        "offices.Office",
        verbose_name=_("oficina"),
        on_delete=models.PROTECT,
        related_name="invoices",
        help_text=_("La de la reserva. Decide quien puede ver la factura."),
    )
    rectifies = models.ForeignKey(
        "self",
        verbose_name=_("rectifica a"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rectifications",
    )
    rectification_reason = models.TextField(_("motivo de la rectificacion"), blank=True, default="")

    # --- datos fiscales copiados al emitir ------------------------------------
    issuer_name = models.CharField(_("emisor"), max_length=160)
    issuer_tax_id = models.CharField(_("NIF del emisor"), max_length=12)
    issuer_address = models.CharField(_("direccion del emisor"), max_length=300)
    customer_name = models.CharField(_("cliente"), max_length=200)
    customer_tax_id = models.CharField(_("NIF del cliente"), max_length=20)
    customer_address = models.CharField(
        _("direccion del cliente"), max_length=300, blank=True, default=""
    )

    # --- importes: suma de las lineas -----------------------------------------
    base_amount = models.DecimalField(_("base imponible"), max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(_("impuestos"), max_digits=10, decimal_places=2)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default="EUR")

    #: Politicas de la empresa tal como estaban al emitir: [{"title", "body"}].
    policies = models.JSONField(_("politicas impresas"), default=list, blank=True)

    # --- Verifactu --------------------------------------------------------------
    hash_anterior = models.CharField(_("huella anterior"), max_length=64, blank=True, default="")
    hash_actual = models.CharField(_("huella"), max_length=64, unique=True)
    qr_data = models.CharField(_("contenido del QR"), max_length=400)
    fecha_registro = models.DateTimeField(_("fecha del registro"))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("emitida por"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices_issued",
    )

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        verbose_name = _("factura")
        verbose_name_plural = _("facturas")
        ordering = ["-issued_at", "-id"]
        # Ni se cambia ni se borra: se corrige con una rectificativa.
        default_permissions = ("add",)
        permissions = [
            ("rectify_invoice", _("Puede emitir facturas rectificativas")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "scope", "sequence"],
                name="billing_factura_correlativo_unico",
            ),
            CheckConstraint(
                condition=(
                    Q(kind=InvoiceKind.ORDINARY, rectifies__isnull=True)
                    | Q(kind=InvoiceKind.RECTIFYING, rectifies__isnull=False)
                ),
                name="billing_rectificativa_apunta_a_su_original",
            ),
        ]
        indexes = [
            models.Index(fields=["office", "issued_at"], name="billing_factura_oficina_fecha"),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise InvoiceImmutable(
                f"La factura {self.number} ya esta emitida: se corrige con una rectificativa."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise InvoiceImmutable(f"La factura {self.number} no se borra: emite una rectificativa.")

    @property
    def verifactu_type(self) -> str:
        return TIPO_VERIFACTU[self.kind]

    @property
    def is_rectifying(self) -> bool:
        return self.kind == InvoiceKind.RECTIFYING


class InvoiceLine(models.Model):
    """Una linea de la factura, con base, impuesto y total propios."""

    invoice = models.ForeignKey(
        Invoice, verbose_name=_("factura"), on_delete=models.PROTECT, related_name="lines"
    )
    position = models.PositiveSmallIntegerField(_("orden"))
    concept = models.CharField(_("concepto"), max_length=200)
    quantity = models.DecimalField(_("cantidad"), max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(_("precio unitario"), max_digits=10, decimal_places=2)
    tax_rate = models.DecimalField(_("tipo de impuesto"), max_digits=5, decimal_places=2)
    base_amount = models.DecimalField(_("base imponible"), max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(_("impuesto"), max_digits=10, decimal_places=2)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = _("linea de factura")
        verbose_name_plural = _("lineas de factura")
        ordering = ["invoice", "position"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "position"], name="billing_linea_orden_unico"
            ),
        ]

    def __str__(self):
        return f"{self.invoice_id} #{self.position} {self.concept}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise InvoiceImmutable("Una linea de factura emitida no se toca.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise InvoiceImmutable("Una linea de factura emitida no se borra.")
