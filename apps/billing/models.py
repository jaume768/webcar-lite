"""Cobros.

Un cobro es un hecho: ocurrio, con una fecha, un importe y un medio de pago.
Por eso no se edita ni se borra nunca: si esta mal, se corrige con un apunte
contrario, igual que en contabilidad. Una fila borrada aqui es dinero que
desaparece del arqueo.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


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
