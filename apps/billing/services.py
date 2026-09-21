"""Reglas de los cobros y las facturas. Las vistas orquestan, aqui se decide."""

from dataclasses import dataclass
from decimal import Decimal

import structlog
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.auditlog import services as audit
from apps.auditlog.models import AuditAction
from apps.core.services import ServiceError

from .models import DEPOSIT_TYPES, OUTGOING_TYPES, Payment, PaymentType
from .selectors import CERO, deposit_held, pending_amount

logger = structlog.get_logger(__name__)

PERMISO_SOBREPAGO = "billing.allow_overpayment"


class BillingServiceError(ServiceError):
    """Regla de negocio incumplida al registrar un cobro."""


class OverpaymentNotAllowed(BillingServiceError):
    """El cobro pasa del pendiente y quien lo intenta no puede autorizarlo."""


def _normalizar_importe(amount: Decimal, payment_type: str) -> Decimal:
    """El signo lo manda el concepto, no quien teclea.

    En el mostrador se escribe "50" tanto para cobrar como para devolver; es el
    concepto elegido el que decide si eso entra o sale.
    """
    importe = abs(Decimal(amount))
    if importe == 0:
        raise BillingServiceError(_("Un cobro de cero euros no es un cobro."))
    return -importe if payment_type in OUTGOING_TYPES else importe


@transaction.atomic
def register_payment(
    *,
    reservation,
    amount: Decimal,
    method: str,
    payment_type: str = PaymentType.PAYMENT,
    office=None,
    paid_at=None,
    reference: str = "",
    notes: str = "",
    allow_overpayment: bool = False,
    actor=None,
    from_gateway: bool = False,
) -> Payment:
    """Registra un movimiento de dinero de una reserva.

    Comprueba dos cosas antes de escribir: que el signo cuadre con el concepto
    y que un cobro del alquiler no se pase del pendiente sin que alguien con
    permiso lo autorice a proposito.
    """
    importe = _normalizar_importe(amount, payment_type)

    if payment_type == PaymentType.DEPOSIT_RETURN:
        retenido = deposit_held(reservation)
        if abs(importe) > retenido:
            raise BillingServiceError(
                _("No se pueden devolver %(importe)s EUR: solo hay %(retenido)s retenidos.")
                % {"importe": abs(importe), "retenido": retenido}
            )

    # La fianza no toca el saldo del alquiler, asi que no se compara con el
    # pendiente: es dinero retenido, no cobrado.
    # Un cobro que confirma una pasarela ya se ha cobrado de verdad: negarse a
    # apuntarlo no lo deshace, solo lo esconde. Se apunta y se avisa.
    if payment_type not in DEPOSIT_TYPES and importe > 0 and from_gateway:
        pendiente = pending_amount(reservation)
        if importe > pendiente:
            logger.warning(
                "pago_online_por_encima_del_pendiente",
                reservation_number=reservation.number,
                importe=str(importe),
                pendiente=str(pendiente),
            )
    elif payment_type not in DEPOSIT_TYPES and importe > 0:
        pendiente = pending_amount(reservation)
        if importe > pendiente:
            if not allow_overpayment:
                raise OverpaymentNotAllowed(
                    _(
                        "El cobro de %(importe)s EUR pasa del pendiente (%(pendiente)s EUR). "
                        "Hace falta confirmarlo."
                    )
                    % {"importe": importe, "pendiente": pendiente}
                )
            if actor is None or not actor.has_perm(PERMISO_SOBREPAGO):
                raise PermissionDenied(
                    _("Tu usuario no puede cobrar por encima del pendiente (%(permiso)s).")
                    % {"permiso": PERMISO_SOBREPAGO}
                )
            logger.warning(
                "cobro_por_encima_del_pendiente",
                reservation_number=reservation.number,
                importe=str(importe),
                pendiente=str(pendiente),
                actor_id=actor.pk,
            )

    pago = Payment(
        reservation=reservation,
        amount=importe,
        method=method,
        payment_type=payment_type,
        office=office or reservation.pickup_office,
        paid_at=paid_at,
        reference=reference.strip(),
        notes=notes,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    if paid_at is None:
        # Deja que el modelo ponga la hora por defecto.
        pago.paid_at = Payment._meta.get_field("paid_at").get_default()
    pago.full_clean(exclude=["reservation", "office", "created_by"])
    pago.save()

    audit.record(
        AuditAction.PAYMENT,
        _("%(concepto)s de %(importe)s € en %(numero)s (%(medio)s)")
        % {
            "concepto": pago.get_payment_type_display(),
            "importe": pago.amount,
            "numero": reservation.number,
            "medio": pago.get_method_display(),
        },
        obj=pago,
        actor=actor,
        reservation=reservation,
        changes={"amount": pago.amount, "method": method, "type": payment_type},
    )
    logger.info(
        "cobro_registrado",
        reservation_number=reservation.number,
        payment_id=pago.pk,
        importe=str(importe),
        metodo=method,
        concepto=payment_type,
        office_id=pago.office_id,
        actor_id=getattr(actor, "pk", None),
    )
    return pago


@transaction.atomic
def refund(*, payment: Payment, amount: Decimal | None = None, reason: str = "", actor=None):
    """Devuelve dinero de un cobro anterior con un apunte contrario.

    El cobro original no se toca: en caja tiene que seguir viendose lo que
    entro y, aparte, lo que salio.
    """
    if payment.amount <= 0:
        raise BillingServiceError(_("Ese apunte ya es una salida de dinero."))

    importe = abs(Decimal(amount)) if amount is not None else payment.amount
    if importe > payment.amount:
        raise BillingServiceError(
            _("No se puede devolver mas de lo cobrado (%(cobrado)s EUR).")
            % {"cobrado": payment.amount}
        )

    tipo = PaymentType.DEPOSIT_RETURN if payment.is_deposit else PaymentType.REFUND
    return register_payment(
        reservation=payment.reservation,
        amount=importe,
        method=payment.method,
        payment_type=tipo,
        office=payment.office,
        reference=payment.reference,
        notes=reason,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------

PERMISO_EMITIR = "billing.add_invoice"
PERMISO_RECTIFICAR = "billing.rectify_invoice"

#: Clave del bloqueo de la cadena de Verifactu. La cadena es una sola por
#: emisor y cruza todas las series, asi que no basta con bloquear la serie: dos
#: facturas de series distintas emitidas a la vez leerian la misma "anterior".
BLOQUEO_CADENA = 7_461_022_301

#: NIF que deja `CompanySettings.load()` cuando nadie ha rellenado la empresa.
NIF_SIN_CONFIGURAR = "00000000"


class InvoiceServiceError(BillingServiceError):
    """No se dan las condiciones para emitir o rectificar la factura."""


@dataclass(frozen=True)
class LineaFactura:
    """Una linea antes de existir: la vista previa y la emision usan la misma."""

    concept: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    base_amount: Decimal
    tax_amount: Decimal
    total: Decimal

    def negada(self) -> "LineaFactura":
        """La misma linea en negativo: lo que anula una rectificativa."""
        return LineaFactura(
            concept=self.concept,
            quantity=self.quantity,
            unit_price=-self.unit_price,
            tax_rate=self.tax_rate,
            base_amount=-self.base_amount,
            tax_amount=-self.tax_amount,
            total=-self.total,
        )


def _d(valor) -> Decimal:
    return Decimal(str(valor or "0"))


def invoice_lines_for(reservation) -> list[LineaFactura]:
    """Lineas de la factura de una reserva: el desglose congelado y los cargos.

    No se recalcula nada: se factura lo que se vendio (el desglose guardado en
    la reserva) mas los cargos de la devolucion. Cada linea trae ya su base,
    impuesto y total.
    """
    lineas = [
        LineaFactura(
            concept=linea["concept"],
            quantity=_d(linea["quantity"]),
            unit_price=_d(linea["unit_price"]),
            tax_rate=_d(linea["tax_rate"]),
            base_amount=_d(linea["base"]),
            tax_amount=_d(linea["tax_amount"]),
            total=_d(linea["total"]),
        )
        for linea in reservation.price_breakdown.get("lines", [])
    ]
    lineas.extend(
        LineaFactura(
            concept=cargo.concept,
            quantity=cargo.quantity,
            unit_price=cargo.unit_price,
            tax_rate=cargo.tax_rate,
            base_amount=cargo.base_amount,
            tax_amount=cargo.tax_amount,
            total=cargo.total,
        )
        for cargo in reservation.charges.order_by("created_at", "pk")
    )
    return lineas


def totals_of(lineas) -> tuple[Decimal, Decimal, Decimal]:
    """Base, impuesto y total de la factura: la suma de sus lineas, nada mas."""
    return (
        sum((linea.base_amount for linea in lineas), CERO),
        sum((linea.tax_amount for linea in lineas), CERO),
        sum((linea.total for linea in lineas), CERO),
    )


def default_series(kind):
    from .models import InvoiceSeries

    return InvoiceSeries.objects.filter(kind=kind, is_active=True, is_default=True).first()


def _empresa_facturable():
    """Datos del emisor, o un error claro si la empresa esta sin configurar."""
    from apps.settings_app.models import CompanySettings

    empresa = CompanySettings.load()
    if empresa.tax_id in ("", NIF_SIN_CONFIGURAR) or not empresa.address.strip():
        raise InvoiceServiceError(
            _(
                "Faltan los datos fiscales de la empresa (NIF y direccion). "
                "Rellenalos en Configuracion antes de emitir facturas."
            )
        )
    return empresa


def _direccion_del_cliente(cliente) -> str:
    partes = [cliente.address, f"{cliente.postal_code} {cliente.city}".strip()]
    if cliente.country and cliente.country != "ES":
        partes.append(cliente.country)
    return ", ".join(parte for parte in partes if parte)


def _siguiente_numero(series, momento) -> tuple[str, int, str]:
    """Numero siguiente de la serie, sin huecos.

    Se bloquea la fila de la serie: dos emisiones a la vez hacen cola aqui y
    nunca comparten numero. Si la transaccion se deshace, el contador tambien.
    """
    from .models import InvoiceSeries, InvoiceSeriesCounter

    serie = InvoiceSeries.objects.select_for_update().get(pk=series.pk)
    ambito = serie.scope_for(momento)
    contador, _creado = InvoiceSeriesCounter.objects.get_or_create(series=serie, scope=ambito)
    contador = InvoiceSeriesCounter.objects.select_for_update().get(pk=contador.pk)
    contador.last_number += 1
    contador.save(update_fields=["last_number"])
    numero = serie.format_number(scope=ambito, sequence=contador.last_number)
    return ambito, contador.last_number, numero


def _huella_anterior() -> str:
    """Huella de la ultima factura de la cadena, con la cadena bloqueada.

    El bloqueo consultivo de Postgres dura hasta el final de la transaccion: la
    siguiente emision espera a que esta haya escrito su huella.
    """
    from django.db import connection

    from .models import Invoice

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BLOQUEO_CADENA])
    ultima = Invoice.objects.order_by("-fecha_registro", "-id").only("hash_actual").first()
    return ultima.hash_actual if ultima else ""


def _crear_factura(
    *,
    reservation,
    series,
    kind,
    lineas: list[LineaFactura],
    empresa,
    rectifies=None,
    reason: str = "",
    actor=None,
    customer=None,
    office=None,
    notes: str = "",
):
    """Numera, encadena y guarda la factura con sus lineas. Todo o nada.

    De una reserva salen el cliente y la oficina; una factura libre los trae
    aparte.
    """
    from apps.settings_app.models import policies_for_invoice

    from . import verifactu
    from .models import TIPO_VERIFACTU, Invoice, InvoiceLine

    ahora = timezone.now()
    ambito, correlativo, numero = _siguiente_numero(series, ahora)
    base, cuota, total = totals_of(lineas)
    anterior = _huella_anterior()
    fecha_expedicion = timezone.localdate(ahora)
    cliente = customer or reservation.customer
    oficina = office or reservation.pickup_office

    factura = Invoice(
        series=series,
        scope=ambito,
        sequence=correlativo,
        number=numero,
        kind=kind,
        issued_at=ahora,
        reservation=reservation,
        customer=cliente,
        office=oficina,
        rectifies=rectifies,
        rectification_reason=reason,
        notes=notes,
        issuer_name=empresa.legal_name,
        issuer_tax_id=empresa.tax_id,
        issuer_address=empresa.full_address,
        customer_name=cliente.full_name,
        customer_tax_id=cliente.document_number,
        customer_address=_direccion_del_cliente(cliente),
        base_amount=base,
        tax_amount=cuota,
        total=total,
        currency=settings.DEFAULT_CURRENCY,
        policies=policies_for_invoice(),
        hash_anterior=anterior,
        hash_actual=verifactu.huella_de_alta(
            nif_emisor=empresa.tax_id,
            numero=numero,
            fecha_expedicion=fecha_expedicion,
            tipo_factura=TIPO_VERIFACTU[kind],
            cuota_total=cuota,
            importe_total=total,
            huella_anterior=anterior,
            generado_en=timezone.localtime(ahora),
        ),
        qr_data=verifactu.url_qr(
            nif_emisor=empresa.tax_id,
            numero=numero,
            fecha_expedicion=fecha_expedicion,
            importe_total=total,
        ),
        fecha_registro=ahora,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    factura.save()
    InvoiceLine.objects.bulk_create(
        InvoiceLine(
            invoice=factura,
            position=posicion,
            concept=linea.concept[:200],
            quantity=linea.quantity,
            unit_price=linea.unit_price,
            tax_rate=linea.tax_rate,
            base_amount=linea.base_amount,
            tax_amount=linea.tax_amount,
            total=linea.total,
        )
        for posicion, linea in enumerate(lineas, start=1)
    )
    return factura


def _enviar_factura(factura, actor=None) -> None:
    """La factura al cliente por correo, con el PDF, si esta activado."""
    from apps.notifications.models import EmailKind
    from apps.notifications.services import queue_email

    queue_email(
        kind=EmailKind.INVOICE,
        reservation=factura.reservation,
        customer=factura.customer,
        context={"invoice_id": factura.pk},
        actor=actor,
    )


def _exigir_permiso(actor, permiso: str, mensaje) -> None:
    if actor is None or not actor.has_perm(permiso):
        raise PermissionDenied(mensaje % {"permiso": permiso})


@transaction.atomic
def issue_invoice(*, reservation, series=None, actor=None):
    """Emite la factura de una reserva finalizada.

    La reserva se bloquea mientras tanto: nadie puede cambiarle el precio ni
    emitirle otra factura a la vez. El total facturado tiene que cuadrar con
    lo que debe el cliente; si no cuadra, no se emite nada.
    """
    from apps.reservations.models import Reservation, ReservationStatus

    from .models import InvoiceKind
    from .selectors import issued_invoice_for

    _exigir_permiso(actor, PERMISO_EMITIR, _("Tu usuario no puede emitir facturas (%(permiso)s)."))

    reserva = (
        Reservation.objects.select_for_update(of=("self",))
        .select_related("customer", "pickup_office")
        .get(pk=reservation.pk)
    )
    if reserva.status != ReservationStatus.FINISHED:
        raise InvoiceServiceError(
            _("Solo se facturan reservas finalizadas: la %(numero)s esta %(estado)s.")
            % {"numero": reserva.number, "estado": reserva.get_status_display().lower()}
        )
    existente = issued_invoice_for(reserva)
    if existente is not None:
        raise InvoiceServiceError(
            _("La reserva %(reserva)s ya tiene la factura %(factura)s en vigor.")
            % {"reserva": reserva.number, "factura": existente.number}
        )
    if reserva.customer_id is None:
        raise InvoiceServiceError(_("Una factura necesita un cliente con sus datos fiscales."))

    lineas = invoice_lines_for(reserva)
    if not lineas:
        raise InvoiceServiceError(_("La reserva no tiene nada que facturar."))
    _base, _cuota, total = totals_of(lineas)
    if total != reserva.grand_total:
        # Defensa: el desglose y los totales de la reserva dicen cosas
        # distintas. Mejor no emitir que emitir un numero que no cuadra.
        logger.error(
            "factura_no_cuadra",
            reservation_number=reserva.number,
            total_lineas=str(total),
            total_reserva=str(reserva.grand_total),
        )
        raise InvoiceServiceError(
            _(
                "Las lineas suman %(lineas)s € y la reserva debe %(reserva)s €. "
                "Recalcula el precio de la reserva antes de facturar."
            )
            % {"lineas": total, "reserva": reserva.grand_total}
        )
    if total <= 0:
        raise InvoiceServiceError(_("No se emite una factura de importe cero o negativo."))

    serie = series or default_series(InvoiceKind.ORDINARY)
    if serie is None or not serie.is_active or serie.kind != InvoiceKind.ORDINARY:
        raise InvoiceServiceError(
            _("No hay una serie de facturas ordinarias activa. Crea una en Series.")
        )

    factura = _crear_factura(
        reservation=reserva,
        series=serie,
        kind=InvoiceKind.ORDINARY,
        lineas=lineas,
        empresa=_empresa_facturable(),
        actor=actor,
    )
    audit.record(
        AuditAction.INVOICE,
        _("Factura %(numero)s emitida (%(total)s €)")
        % {"numero": factura.number, "total": factura.total},
        obj=factura,
        actor=actor,
        reservation=factura.reservation,
        office_id=factura.office_id,
    )
    _enviar_factura(factura, actor)
    logger.info(
        "factura_emitida",
        invoice_number=factura.number,
        reservation_number=reserva.number,
        total=str(factura.total),
        actor_id=getattr(actor, "pk", None),
    )
    return factura


@transaction.atomic
def rectify_invoice(*, invoice, reason: str, series=None, actor=None):
    """Anula una factura con una rectificativa que la deja a cero.

    La original no se toca: la rectificativa repite sus lineas en negativo y
    apunta a ella. Despues, la reserva queda libre para corregirse y volver a
    facturarse con un numero nuevo.
    """
    from .models import Invoice, InvoiceKind

    _exigir_permiso(
        actor, PERMISO_RECTIFICAR, _("Tu usuario no puede rectificar facturas (%(permiso)s).")
    )
    motivo = (reason or "").strip()
    if not motivo:
        raise InvoiceServiceError(_("Una rectificativa no se emite sin explicar el motivo."))

    original = (
        Invoice.objects.select_for_update(of=("self",))
        .select_related("customer", "office")
        .get(pk=invoice.pk)
    )
    if original.is_rectifying:
        raise InvoiceServiceError(
            _("Una rectificativa no se rectifica: emite otra sobre la original.")
        )
    if original.rectifications.exists():
        raise InvoiceServiceError(
            _("La factura %(numero)s ya esta rectificada.") % {"numero": original.number}
        )

    serie = series or default_series(InvoiceKind.RECTIFYING)
    if serie is None or not serie.is_active or serie.kind != InvoiceKind.RECTIFYING:
        raise InvoiceServiceError(
            _("No hay una serie de rectificativas activa. Crea una en Series.")
        )

    lineas = [
        LineaFactura(
            concept=linea.concept,
            quantity=linea.quantity,
            unit_price=linea.unit_price,
            tax_rate=linea.tax_rate,
            base_amount=linea.base_amount,
            tax_amount=linea.tax_amount,
            total=linea.total,
        ).negada()
        for linea in original.lines.all()
    ]
    rectificativa = _crear_factura(
        reservation=original.reservation,
        series=serie,
        kind=InvoiceKind.RECTIFYING,
        lineas=lineas,
        empresa=_empresa_facturable(),
        rectifies=original,
        reason=motivo,
        actor=actor,
        customer=original.customer,
        office=original.office,
    )
    audit.record(
        AuditAction.INVOICE,
        _("Rectificativa %(rectificativa)s anula %(numero)s: %(motivo)s")
        % {"rectificativa": rectificativa.number, "numero": original.number, "motivo": motivo},
        obj=rectificativa,
        actor=actor,
        reservation=rectificativa.reservation,
        office_id=rectificativa.office_id,
    )
    logger.warning(
        "factura_rectificada",
        invoice_number=original.number,
        rectifying_number=rectificativa.number,
        motivo=motivo,
        actor_id=getattr(actor, "pk", None),
    )
    return rectificativa


def free_line(*, concept: str, quantity, unit_price, tax_rate) -> LineaFactura:
    """Linea de una factura libre, con base, cuota y total ya redondeados.

    Mismo criterio que el motor de tarifas: se redondea la base y la cuota por
    separado (ROUND_HALF_UP a centimos) y el total es su suma.
    """
    from decimal import ROUND_HALF_UP

    centimos = Decimal("0.01")
    cantidad = Decimal(str(quantity))
    precio = Decimal(str(unit_price)).quantize(centimos, rounding=ROUND_HALF_UP)
    tipo = Decimal(str(tax_rate))
    base = (cantidad * precio).quantize(centimos, rounding=ROUND_HALF_UP)
    cuota = (base * tipo / Decimal("100")).quantize(centimos, rounding=ROUND_HALF_UP)
    return LineaFactura(
        concept=(concept or "").strip(),
        quantity=cantidad,
        unit_price=precio,
        tax_rate=tipo,
        base_amount=base,
        tax_amount=cuota,
        total=base + cuota,
    )


@transaction.atomic
def issue_manual_invoice(
    *, customer, office, lines: list[LineaFactura], series=None, notes: str = "", actor=None
):
    """Factura a un cliente sin reserva detras: una multa, un dano, un cargo suelto.

    Mismas reglas que cualquier factura: numeracion sin huecos, huella
    encadenada, datos fiscales copiados e inmutable desde que existe.
    """
    from apps.offices.selectors import offices_for_user

    from .models import InvoiceKind

    _exigir_permiso(actor, PERMISO_EMITIR, _("Tu usuario no puede emitir facturas (%(permiso)s)."))
    if customer is None:
        raise InvoiceServiceError(_("Una factura necesita un cliente con sus datos fiscales."))
    if not customer.document_number:
        raise InvoiceServiceError(_("El cliente no tiene NIF ni documento: complétalo antes."))
    if office is None or not offices_for_user(actor).filter(pk=office.pk).exists():
        raise PermissionDenied(_("No puedes facturar en esa oficina."))
    lineas = [linea for linea in lines if linea.concept]
    if not lineas:
        raise InvoiceServiceError(_("Añade al menos una línea con concepto."))
    _base, _cuota, total = totals_of(lineas)
    if total <= 0:
        raise InvoiceServiceError(_("No se emite una factura de importe cero o negativo."))

    serie = series or default_series(InvoiceKind.ORDINARY)
    if serie is None or not serie.is_active or serie.kind != InvoiceKind.ORDINARY:
        raise InvoiceServiceError(
            _("No hay una serie de facturas ordinarias activa. Crea una en Series.")
        )

    factura = _crear_factura(
        reservation=None,
        series=serie,
        kind=InvoiceKind.ORDINARY,
        lineas=lineas,
        empresa=_empresa_facturable(),
        actor=actor,
        customer=customer,
        office=office,
        notes=notes.strip(),
    )
    audit.record(
        AuditAction.INVOICE,
        _("Factura libre %(numero)s a %(cliente)s (%(total)s €)")
        % {"numero": factura.number, "cliente": customer.full_name, "total": factura.total},
        obj=factura,
        actor=actor,
        office_id=office.pk,
    )
    _enviar_factura(factura, actor)
    logger.info(
        "factura_libre_emitida",
        invoice_number=factura.number,
        customer_id=customer.pk,
        total=str(factura.total),
        actor_id=getattr(actor, "pk", None),
    )
    return factura


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


def validate_number_format(formato: str) -> str:
    """Comprueba que el formato numera de verdad y no se come el correlativo."""
    formato = (formato or "").strip()
    if "{sequence" not in formato:
        raise InvoiceServiceError(_("El formato tiene que llevar {sequence}, el correlativo."))
    try:
        ejemplo = formato.format(year="2026", sequence=1)
        otro = formato.format(year="2026", sequence=2)
    except (KeyError, IndexError, ValueError):
        raise InvoiceServiceError(
            _(
                "Formato no valido. Solo se admiten {year} y {sequence}, "
                "por ejemplo F{year}-{sequence:05d}."
            )
        ) from None
    if ejemplo == otro or len(ejemplo) > 40:
        raise InvoiceServiceError(_("Ese formato no distingue una factura de la siguiente."))
    return formato


@transaction.atomic
def save_series(*, series, actor=None):
    """Crea o actualiza una serie ya validada por su formulario.

    Una serie con facturas no cambia de tipo ni de formato: los numeros ya
    emitidos dejarian de seguir la misma pauta. Si se marca por defecto, la
    que lo era de su tipo deja de serlo.
    """
    from .models import InvoiceSeries

    creando = series.pk is None
    series.code = (series.code or "").strip().lower()
    series.number_format = validate_number_format(series.number_format)

    if not creando:
        anterior = InvoiceSeries.objects.select_for_update().get(pk=series.pk)
        cambia_numeracion = (
            anterior.kind != series.kind or anterior.number_format != series.number_format
        )
        if cambia_numeracion and anterior.invoices.exists():
            raise InvoiceServiceError(
                _(
                    "La serie %(serie)s ya tiene facturas: no se le cambia el tipo ni el "
                    "formato. Crea una serie nueva."
                )
                % {"serie": anterior.code}
            )
    if series.is_default:
        if not series.is_active:
            raise InvoiceServiceError(_("Una serie desactivada no puede ser la de por defecto."))
        InvoiceSeries.objects.filter(kind=series.kind, is_default=True).exclude(
            pk=series.pk
        ).update(is_default=False)

    series.save()
    logger.info(
        "serie_creada" if creando else "serie_actualizada",
        series_id=series.pk,
        code=series.code,
        actor_id=getattr(actor, "pk", None),
    )
    return series


@transaction.atomic
def set_series_active(*, series, active: bool, actor=None):
    """Alta o baja logica. La de por defecto no se desactiva sin sustituta."""
    if not active and series.is_default:
        raise InvoiceServiceError(
            _("%(serie)s es la serie por defecto. Marca otra como por defecto antes.")
            % {"serie": series.code}
        )
    if active:
        series.activate()
    else:
        series.deactivate()
    logger.info(
        "serie_activada" if active else "serie_desactivada",
        series_id=series.pk,
        actor_id=getattr(actor, "pk", None),
    )
    return series
