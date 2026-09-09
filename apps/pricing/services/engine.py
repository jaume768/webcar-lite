"""Motor de tarifas: de un `PriceQuoteInput` a un `PriceBreakdown`.

Es una funcion pura en el sentido que importa: recibe datos, consulta el
catalogo y devuelve el desglose. No toca la request, ni la sesion, ni guarda
nada. Se puede probar entera sin cliente HTTP y sin que existan las reservas.

Como se redondea (importa, y mucho):

- Se opera con `Decimal` de principio a fin. Ni un float.
- El redondeo se hace **al cerrar cada linea**, con ROUND_HALF_UP: primero la
  base, despues el impuesto sobre esa base ya redondeada.
- Los totales son sumas de lineas ya redondeadas, nunca un calculo aparte. Por
  eso `taxable_base + tax_total == total` cuadra siempre al centimo, y por eso
  el total de la reserva podra ser literalmente la suma de sus lineas cuando se
  guarde en factura.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from ..dto import CENT, PriceBreakdown, PriceLine, PriceQuoteInput
from ..models import (
    AmountType,
    CalculationType,
    Discount,
    Supplement,
    SupplementType,
)
from .duration import rental_days
from .rates import resolve_rate, resolve_season, tier_allocation

CERO = Decimal("0.00")

KIND_RENTAL = "rental"
KIND_EXTRA = "extra"
KIND_SUPPLEMENT = "supplement"
KIND_DISCOUNT = "discount"


def redondear(valor: Decimal) -> Decimal:
    """Al centimo, ROUND_HALF_UP. La unica forma de redondear del sistema."""
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def construir_linea(
    *,
    kind: str,
    concept: str,
    quantity: Decimal,
    unit_price: Decimal,
    tax_rate: Decimal,
    base: Decimal | None = None,
    source_code: str = "",
) -> PriceLine:
    """Cierra una linea: redondea base, calcula impuesto y total.

    `base` se pasa aparte cuando no es `unit_price * quantity` (un extra con
    tope, por ejemplo, o un descuento).
    """
    base_final = redondear(base if base is not None else unit_price * quantity)
    impuesto = redondear(base_final * tax_rate / Decimal("100"))
    return PriceLine(
        kind=kind,
        concept=concept,
        quantity=quantity,
        unit_price=redondear(unit_price),
        base=base_final,
        tax_rate=tax_rate,
        tax_amount=impuesto,
        total=base_final + impuesto,
        source_code=source_code,
    )


# ---------------------------------------------------------------------------
# Piezas del calculo
# ---------------------------------------------------------------------------


def _lineas_de_alquiler(rate, days, manual_override, tax_rate, warnings):
    """Lineas del alquiler en si, y el tramo que se ha aplicado."""
    if manual_override is not None:
        # Precio forzado a mano: se respeta, pero queda dicho en el desglose.
        # Quien tiene permiso para tocarlo lo sabe; quien lea la reserva manana,
        # tambien.
        warnings.append(
            str(
                _("Precio por dia forzado a mano: %(precio)s EUR.")
                % {"precio": redondear(manual_override)}
            )
        )
        linea = construir_linea(
            kind=KIND_RENTAL,
            concept=str(_("Alquiler (%(dias)s dias, precio manual)") % {"dias": days}),
            quantity=Decimal(days),
            unit_price=manual_override,
            tax_rate=tax_rate,
            source_code=rate.code if rate else "",
        )
        return [linea], None

    reparto = tier_allocation(rate, days)
    lineas = []
    for tramo, dias_del_tramo, precio_dia in reparto:
        concepto = (
            str(_("Alquiler %(dias)s dias") % {"dias": dias_del_tramo})
            if len(reparto) == 1
            else str(
                _("Alquiler %(dias)s dias (tramo %(tramo)s)")
                % {"dias": dias_del_tramo, "tramo": tramo}
            )
        )
        lineas.append(
            construir_linea(
                kind=KIND_RENTAL,
                concept=concepto,
                quantity=Decimal(dias_del_tramo),
                unit_price=precio_dia,
                tax_rate=tax_rate,
                source_code=rate.code,
            )
        )
    # El tramo "aplicado" que se ensena es el de la duracion total: en modo
    # progresivo hay varios, y el ultimo es el que explica el precio del dia.
    tramo_aplicado = reparto[-1][0]
    return lineas, tramo_aplicado


def _lineas_de_extras(extras, days, warnings):
    """Una linea por extra pedido, con su tope si lo tiene."""
    lineas = []
    for peticion in extras:
        extra = peticion.extra
        cantidad = max(1, int(peticion.quantity))
        if cantidad > extra.max_quantity:
            warnings.append(
                str(
                    _("%(extra)s admite como mucho %(max)s unidades: se ajusta la cantidad.")
                    % {"extra": extra.name, "max": extra.max_quantity}
                )
            )
            cantidad = extra.max_quantity

        if extra.calculation_type == CalculationType.PER_DAY:
            importe_unidad = extra.price * days
            if extra.max_amount is not None and importe_unidad > extra.max_amount:
                # El tope es por unidad: dos sillas con tope de 30 son 60, que
                # es lo que espera el cliente cuando alquila dos sillas.
                importe_unidad = extra.max_amount
            concepto = str(_("%(extra)s (%(dias)s dias)") % {"extra": extra.name, "dias": days})
            base = importe_unidad * cantidad
        elif extra.calculation_type == CalculationType.PER_RESERVATION:
            # Por reserva: se cobra una vez, da igual cuantas unidades se pidan.
            cantidad = 1
            importe_unidad = extra.price
            concepto = extra.name
            base = importe_unidad
        else:  # ONCE: precio unico por unidad
            importe_unidad = extra.price
            concepto = extra.name
            base = importe_unidad * cantidad

        lineas.append(
            construir_linea(
                kind=KIND_EXTRA,
                concept=concepto,
                quantity=Decimal(cantidad),
                unit_price=importe_unidad,
                base=base,
                tax_rate=extra.tax_rate,
                source_code=extra.code,
            )
        )
    return lineas


def _mismo_pool(pickup_office, return_office) -> bool:
    """True si los dos sitios comparten flota.

    Dos oficinas sin pool son dos sitios distintos aunque las dos tengan el pool
    a vacio: `None == None` no significa "mismo grupo".
    """
    if pickup_office.pk == return_office.pk:
        return True
    return pickup_office.pool_id is not None and pickup_office.pool_id == return_office.pool_id


def _aplica_suplemento(suplemento, quote: PriceQuoteInput) -> bool:
    """Condicion de cada tipo de suplemento. Toda la logica esta aqui."""
    tipo = suplemento.supplement_type

    if tipo == SupplementType.ONE_WAY:
        return not _mismo_pool(quote.pickup_office, quote.return_office)

    if tipo == SupplementType.YOUNG_DRIVER:
        if quote.customer_age is None:
            return False
        desde = suplemento.min_age if suplemento.min_age is not None else 0
        hasta = suplemento.max_age if suplemento.max_age is not None else 200
        return desde <= quote.customer_age <= hasta

    if tipo == SupplementType.AIRPORT:
        oficinas = {oficina.pk for oficina in suplemento.offices.all()}
        return quote.pickup_office.pk in oficinas or quote.return_office.pk in oficinas

    if tipo == SupplementType.AFTER_HOURS:
        if suplemento.hours_from is None or suplemento.hours_to is None:
            return False
        return any(
            not (
                suplemento.hours_from
                <= momento.timetz().replace(tzinfo=None)
                <= suplemento.hours_to
            )
            for momento in (quote.pickup_at, quote.return_at)
        )

    return False


def _lineas_de_suplementos(quote: PriceQuoteInput, base_alquiler: Decimal):
    """Suplementos que se cumplen, calculados sobre la base del alquiler."""
    lineas = []
    suplementos = (
        Supplement.objects.active().prefetch_related("offices").order_by("sort_order", "name")
    )
    for suplemento in suplementos:
        if not _aplica_suplemento(suplemento, quote):
            continue
        if suplemento.amount_type == AmountType.PERCENT:
            base = base_alquiler * suplemento.amount / Decimal("100")
        else:
            base = suplemento.amount
        lineas.append(
            construir_linea(
                kind=KIND_SUPPLEMENT,
                concept=suplemento.name,
                quantity=Decimal(1),
                unit_price=base,
                base=base,
                tax_rate=suplemento.tax_rate,
                source_code=suplemento.code,
            )
        )
    return lineas


def _descuentos_aplicables(quote: PriceQuoteInput, days: int, fecha, warnings):
    """Descuentos automaticos, mas el del codigo si lo hay y es valido."""
    consulta = Discount.objects.active().prefetch_related("categories")
    aplicables = []

    for descuento in consulta:
        vigente = (descuento.valid_from is None or descuento.valid_from <= fecha) and (
            descuento.valid_to is None or fecha <= descuento.valid_to
        )
        if not vigente:
            continue
        if descuento.min_days is not None and days < descuento.min_days:
            continue
        categorias = list(descuento.categories.all())
        if categorias and quote.category.pk not in {c.pk for c in categorias}:
            continue

        if descuento.requires_code:
            if descuento.code.lower() == (quote.discount_code or "").strip().lower():
                aplicables.append(descuento)
        else:
            aplicables.append(descuento)

    if quote.discount_code and not any(d.requires_code for d in aplicables):
        # Un codigo que no vale no puede pasar desapercibido: no es un error que
        # tumbe el presupuesto, pero quien lo ha tecleado tiene que enterarse.
        warnings.append(
            str(
                _("El codigo de descuento '%(codigo)s' no es valido o no se puede aplicar aqui.")
                % {"codigo": quote.discount_code}
            )
        )
    return aplicables


def _lineas_de_descuentos(descuentos, base_alquiler: Decimal, tax_rate: Decimal):
    """Lineas negativas. Nunca dejan la base del alquiler por debajo de cero."""
    lineas = []
    restante = base_alquiler
    for descuento in descuentos:
        if descuento.amount_type == AmountType.PERCENT:
            importe = base_alquiler * descuento.amount / Decimal("100")
        else:
            importe = descuento.amount
        importe = min(redondear(importe), restante)
        if importe <= CERO:
            continue
        restante -= importe
        lineas.append(
            construir_linea(
                kind=KIND_DISCOUNT,
                concept=descuento.name,
                quantity=Decimal(1),
                unit_price=-importe,
                base=-importe,
                tax_rate=tax_rate,
                source_code=descuento.code,
            )
        )
    return lineas


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def calculate_reservation_price(quote: PriceQuoteInput) -> PriceBreakdown:
    """Precio de un alquiler, con el desglose entero.

    Orden del calculo, que es tambien el orden en el que se lee la factura:

    1. dias facturables (`rental_days`);
    2. temporada de la fecha de recogida y tarifa que le corresponde;
    3. base del alquiler segun los tramos (plano o progresivo);
    4. extras, cada uno con su forma de cobro y su tope;
    5. suplementos, calculados sobre la base del alquiler;
    6. descuentos, como lineas negativas;
    7. totales, sumando lineas ya redondeadas.
    """
    warnings: list[str] = []

    days = rental_days(quote.pickup_at, quote.return_at, courtesy_minutes=quote.courtesy_minutes)
    fecha_referencia = quote.pickup_at.date()
    tax_rate = Decimal(settings.DEFAULT_TAX_RATE)

    season = resolve_season(fecha_referencia)
    if quote.return_at.date() > fecha_referencia and season is not None:
        # La reserva puede cruzar el cambio de temporada. Se avisa, pero el
        # precio no se parte: manda la temporada de la recogida (ver
        # `rates.resolve_season`).
        season_devolucion = resolve_season(quote.return_at.date())
        if season_devolucion is not None and season_devolucion.pk != season.pk:
            warnings.append(
                str(
                    _(
                        "La reserva cruza a la temporada '%(otra)s'. Se cobra entera a "
                        "'%(aplicada)s', la de la fecha de recogida."
                    )
                    % {"otra": season_devolucion.name, "aplicada": season.name}
                )
            )

    rate = quote.rate
    if rate is None and quote.manual_override is None:
        rate = resolve_rate(
            category=quote.category,
            office=quote.pickup_office,
            channel=quote.channel,
            days=days,
            fecha=fecha_referencia,
            season=season,
        )

    lineas_alquiler, tramo = _lineas_de_alquiler(
        rate, days, quote.manual_override, tax_rate, warnings
    )
    base_alquiler = sum((linea.base for linea in lineas_alquiler), CERO)
    precio_dia = redondear(base_alquiler / days) if days else CERO

    lineas_extras = _lineas_de_extras(quote.extras, days, warnings)
    lineas_suplementos = _lineas_de_suplementos(quote, base_alquiler)
    descuentos = _descuentos_aplicables(quote, days, fecha_referencia, warnings)
    lineas_descuentos = _lineas_de_descuentos(descuentos, base_alquiler, tax_rate)

    lineas = [*lineas_alquiler, *lineas_extras, *lineas_suplementos, *lineas_descuentos]

    taxable_base = sum((linea.base for linea in lineas), CERO)
    tax_total = sum((linea.tax_amount for linea in lineas), CERO)

    return PriceBreakdown(
        rental_days=days,
        applied_rate=rate,
        applied_tier=tramo,
        applied_season=season,
        daily_price=precio_dia,
        base_amount=base_alquiler,
        lines=tuple(lineas),
        extras_total=sum((linea.base for linea in lineas_extras), CERO),
        supplements_total=sum((linea.base for linea in lineas_suplementos), CERO),
        # En positivo: es lo que se ha descontado. Las lineas van en negativo.
        discounts_total=-sum((linea.base for linea in lineas_descuentos), CERO),
        taxable_base=taxable_base,
        tax_total=tax_total,
        total=taxable_base + tax_total,
        currency=settings.DEFAULT_CURRENCY,
        warnings=tuple(warnings),
    )
