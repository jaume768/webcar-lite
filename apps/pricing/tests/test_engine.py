"""Motor de tarifas: tramos, temporadas, extras, suplementos y descuentos."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.fleet.tests.factories import VehicleCategoryFactory
from apps.offices.tests.factories import OfficeFactory, OfficePoolFactory
from apps.pricing.dto import ExtraRequest, PriceQuoteInput
from apps.pricing.models import AmountType, CalculationType, Extra, SupplementType, TierMode
from apps.pricing.services import (
    AmbiguousRate,
    NoRateAvailable,
    calculate_reservation_price,
)

from .factories import (
    TRAMOS_ESTANDAR,
    DiscountFactory,
    RateFactory,
    SeasonFactory,
    SupplementFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Montaje
# ---------------------------------------------------------------------------


@pytest.fixture
def categoria():
    return VehicleCategoryFactory(code="eco", name="Economico")


@pytest.fixture
def tarifa(categoria):
    return RateFactory(
        code="base", name="Tarifa base", categories=[categoria], tiers=TRAMOS_ESTANDAR
    )


def cita(dia=1, hora=10, minuto=0, mes=6):
    return timezone.make_aware(datetime(2026, mes, dia, hora, minuto))


def consulta(categoria, oficina, *, dias=None, devolucion=None, **extra):
    """Arma un PriceQuoteInput con lo minimo."""
    salida = extra.pop("pickup_at", cita())
    if devolucion is None:
        devolucion = salida + timedelta(days=dias if dias else 1)
    return PriceQuoteInput(
        category=categoria,
        pickup_office=oficina,
        return_office=extra.pop("return_office", oficina),
        pickup_at=salida,
        return_at=devolucion,
        **extra,
    )


# ---------------------------------------------------------------------------
# Tramos en modo PLANO: la tabla del enunciado
# ---------------------------------------------------------------------------

TABLA_PLANA = [
    (1, "50.00"),
    (2, "90.00"),
    (3, "135.00"),
    (4, "160.00"),
    (7, "280.00"),
    (8, "280.00"),
    (14, "490.00"),
    (15, "450.00"),
]


@pytest.mark.parametrize(("dias", "esperado"), TABLA_PLANA)
def test_la_tabla_de_tramos_plana(categoria, palma, tarifa, dias, esperado):
    """Tramos 1/2-3/4-7/8-14/15+ a 50/45/40/35/30, modo plano."""
    resultado = calculate_reservation_price(consulta(categoria, palma, dias=dias))

    assert resultado.rental_days == dias
    assert resultado.base_amount == Decimal(esperado)


def test_el_escalon_de_siete_a_ocho_dias_baja_el_precio(categoria, palma, tarifa):
    """**Esto no es un error y no hay que "arreglarlo".**

    En modo plano, alargar de 7 a 8 dias mete el alquiler entero en un tramo mas
    barato: 7 x 40 = 280 y 8 x 35 = 280. El octavo dia sale gratis, y del noveno
    en adelante sale mas barato que la semana. Es el incentivo a alargar el
    alquiler, y es como funciona el sector.
    """
    siete = calculate_reservation_price(consulta(categoria, palma, dias=7))
    ocho = calculate_reservation_price(consulta(categoria, palma, dias=8))
    nueve = calculate_reservation_price(consulta(categoria, palma, dias=9))

    assert siete.base_amount == ocho.base_amount == Decimal("280.00")
    assert nueve.base_amount == Decimal("315.00")


def test_el_escalon_de_catorce_a_quince_dias_tambien_baja(categoria, palma, tarifa):
    """Mismo caso, y tambien intencionado: 14 x 35 = 490, 15 x 30 = 450."""
    catorce = calculate_reservation_price(consulta(categoria, palma, dias=14))
    quince = calculate_reservation_price(consulta(categoria, palma, dias=15))

    assert catorce.base_amount == Decimal("490.00")
    assert quince.base_amount == Decimal("450.00")
    assert quince.base_amount < catorce.base_amount


def test_cambiar_los_tramos_en_la_base_de_datos_cambia_el_precio(categoria, palma, tarifa):
    """Criterio de aceptacion: sin tocar codigo."""
    antes = calculate_reservation_price(consulta(categoria, palma, dias=5)).base_amount

    tarifa.tiers.filter(min_days=4).update(price_per_day=Decimal("60.00"))

    despues = calculate_reservation_price(consulta(categoria, palma, dias=5)).base_amount
    assert antes == Decimal("200.00")
    assert despues == Decimal("300.00")


# ---------------------------------------------------------------------------
# Modo progresivo
# ---------------------------------------------------------------------------


def test_modo_progresivo_cobra_cada_dia_a_su_tramo(categoria, palma):
    """8 dias = 1x50 + 2x45 + 4x40 + 1x35 = 335."""
    RateTierProgresiva = RateFactory(
        code="progresiva",
        name="Tarifa progresiva",
        categories=[categoria],
        tiers=TRAMOS_ESTANDAR,
        tier_mode=TierMode.PROGRESSIVE,
    )

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=8, rate=RateTierProgresiva)
    )

    assert resultado.base_amount == Decimal("335.00")
    assert len(resultado.lines_of("rental")) == 4


def test_en_progresivo_alargar_nunca_abarata(categoria, palma):
    """La diferencia con el modo plano: aqui no hay escalon a la baja."""
    tarifa = RateFactory(
        code="progresiva",
        categories=[categoria],
        tiers=TRAMOS_ESTANDAR,
        tier_mode=TierMode.PROGRESSIVE,
    )

    totales = [
        calculate_reservation_price(consulta(categoria, palma, dias=d, rate=tarifa)).base_amount
        for d in range(1, 20)
    ]

    assert totales == sorted(totales)


# ---------------------------------------------------------------------------
# Resolucion de tarifa
# ---------------------------------------------------------------------------


def test_sin_tarifa_aplicable_explota(categoria, palma):
    """Obligatorio: NoRateAvailable, nunca un precio 0."""
    with pytest.raises(NoRateAvailable):
        calculate_reservation_price(consulta(categoria, palma, dias=3))


def test_una_tarifa_de_otra_categoria_no_vale(categoria, palma):
    otra = VehicleCategoryFactory(code="suv")
    RateFactory(code="suv-only", categories=[otra], tiers=TRAMOS_ESTANDAR)

    with pytest.raises(NoRateAvailable):
        calculate_reservation_price(consulta(categoria, palma, dias=3))


def test_una_tarifa_de_otra_oficina_no_vale(categoria, palma, alcudia):
    RateFactory(
        code="solo-alcudia", categories=[categoria], offices=[alcudia], tiers=TRAMOS_ESTANDAR
    )

    with pytest.raises(NoRateAvailable):
        calculate_reservation_price(consulta(categoria, palma, dias=3))


def test_gana_la_de_mayor_prioridad(categoria, palma):
    RateFactory(code="general", categories=[categoria], tiers=TRAMOS_ESTANDAR, priority=0)
    RateFactory(
        code="promocion",
        categories=[categoria],
        tiers=[(1, None, "20.00")],
        priority=10,
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3))

    assert resultado.applied_rate.code == "promocion"
    assert resultado.base_amount == Decimal("60.00")


def test_a_igual_prioridad_gana_la_mas_especifica(categoria, palma):
    """La de oficina concreta gana a la de todas las oficinas."""
    RateFactory(code="todas", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    RateFactory(
        code="solo-palma",
        categories=[categoria],
        offices=[palma],
        tiers=[(1, None, "70.00")],
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=2))

    assert resultado.applied_rate.code == "solo-palma"


def test_empate_total_es_un_error_explicito(categoria, palma):
    """Obligatorio por diseno: nunca se elige a ciegas."""
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR)

    with pytest.raises(AmbiguousRate) as error:
        calculate_reservation_price(consulta(categoria, palma, dias=3))

    assert "una" in str(error.value) and "otra" in str(error.value)


def test_el_canal_separa_tarifas(categoria, palma):
    RateFactory(code="web", categories=[categoria], tiers=[(1, None, "25.00")], channel="web")

    with pytest.raises(NoRateAvailable):
        calculate_reservation_price(consulta(categoria, palma, dias=2))

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=2, channel="web"))
    assert resultado.base_amount == Decimal("50.00")


def test_los_dias_minimos_de_la_tarifa_se_respetan(categoria, palma):
    RateFactory(
        code="larga-duracion",
        categories=[categoria],
        tiers=[(1, None, "20.00")],
        min_days=7,
    )

    with pytest.raises(NoRateAvailable):
        calculate_reservation_price(consulta(categoria, palma, dias=3))

    assert calculate_reservation_price(consulta(categoria, palma, dias=10)).base_amount == Decimal(
        "200.00"
    )


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


def test_una_reserva_que_cruza_temporada_se_cobra_a_la_de_recogida(categoria, palma):
    """Obligatorio: la regla es "manda la fecha de recogida", y queda avisada."""
    baja = SeasonFactory(
        code="baja", name="Baja", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)
    )
    alta = SeasonFactory(
        code="alta", name="Alta", start_date=date(2026, 7, 1), end_date=date(2026, 9, 30)
    )
    RateFactory(code="baja-rate", categories=[categoria], season=baja, tiers=[(1, None, "30.00")])
    RateFactory(code="alta-rate", categories=[categoria], season=alta, tiers=[(1, None, "80.00")])

    # Recoge el 28 de junio (baja) y devuelve el 3 de julio (alta): 5 dias.
    resultado = calculate_reservation_price(
        consulta(categoria, palma, pickup_at=cita(dia=28), devolucion=cita(dia=3, mes=7))
    )

    assert resultado.applied_season.code == "baja"
    assert resultado.base_amount == Decimal("150.00")  # 5 dias x 30
    assert any("cruza" in aviso for aviso in resultado.warnings)


def test_gana_la_temporada_de_mas_prioridad(categoria, palma):
    """Semana Santa por encima de temporada media, sin recortar la de debajo."""
    media = SeasonFactory(
        code="media", start_date=date(2026, 3, 1), end_date=date(2026, 6, 30), priority=0
    )
    puente = SeasonFactory(
        code="puente", start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), priority=10
    )
    RateFactory(code="media-rate", categories=[categoria], season=media, tiers=[(1, None, "40.00")])
    RateFactory(
        code="puente-rate", categories=[categoria], season=puente, tiers=[(1, None, "90.00")]
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=2))

    assert resultado.applied_season.code == "puente"
    assert resultado.base_amount == Decimal("180.00")


def test_una_tarifa_sin_temporada_vale_siempre(categoria, palma, tarifa):
    SeasonFactory(code="alta", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), priority=5)

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=2))

    assert resultado.applied_rate.code == "base"
    assert resultado.applied_season.code == "alta"


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------


def crear_extra(**cambios):
    valores = {
        "code": "silla",
        "name": "Silla de bebe",
        "calculation_type": CalculationType.PER_DAY,
        "price": Decimal("5.00"),
        "tax_rate": Decimal("21.00"),
        "max_quantity": 3,
    }
    valores.update(cambios)
    return Extra.objects.create(**valores)


def test_un_extra_por_dia_con_tope(categoria, palma, tarifa):
    """Obligatorio: 10 dias x 5 EUR/dia con tope 30 EUR = 30 EUR."""
    silla = crear_extra(max_amount=Decimal("30.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=10, extras=(ExtraRequest(silla, 1),))
    )

    assert resultado.extras_total == Decimal("30.00")


def test_un_extra_por_dia_sin_tope(categoria, palma, tarifa):
    silla = crear_extra(max_amount=None)

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=10, extras=(ExtraRequest(silla, 1),))
    )

    assert resultado.extras_total == Decimal("50.00")


def test_el_tope_es_por_unidad(categoria, palma, tarifa):
    """Dos sillas son dos topes: el cliente alquila dos sillas, no media."""
    silla = crear_extra(max_amount=Decimal("30.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=10, extras=(ExtraRequest(silla, 2),))
    )

    assert resultado.extras_total == Decimal("60.00")


def test_un_extra_por_reserva_se_cobra_una_vez(categoria, palma, tarifa):
    conductor = crear_extra(
        code="conductor2",
        name="Segundo conductor",
        calculation_type=CalculationType.PER_RESERVATION,
        price=Decimal("25.00"),
    )

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=7, extras=(ExtraRequest(conductor, 2),))
    )

    assert resultado.extras_total == Decimal("25.00")


def test_pasarse_de_la_cantidad_maxima_avisa_y_ajusta(categoria, palma, tarifa):
    silla = crear_extra(max_quantity=2, max_amount=None)

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=1, extras=(ExtraRequest(silla, 9),))
    )

    assert resultado.extras_total == Decimal("10.00")
    assert any("como mucho" in aviso for aviso in resultado.warnings)


# ---------------------------------------------------------------------------
# Suplementos
# ---------------------------------------------------------------------------


def test_one_way_entre_pools_distintos_suma_suplemento(categoria, tarifa):
    """Obligatorio."""
    bahia = OfficePoolFactory(code="bahia")
    norte = OfficePoolFactory(code="norte")
    salida = OfficeFactory(code="palma-pool", pool=bahia)
    llegada = OfficeFactory(code="alcudia-pool", pool=norte)
    SupplementFactory(code="one-way", name="Devolucion en otra oficina", amount=Decimal("50.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, salida, dias=3, return_office=llegada)
    )

    assert resultado.supplements_total == Decimal("50.00")


def test_dentro_del_mismo_pool_no_hay_one_way(categoria, tarifa):
    """Obligatorio: para eso existe el pool."""
    bahia = OfficePoolFactory(code="bahia")
    salida = OfficeFactory(code="palma-pool", pool=bahia)
    llegada = OfficeFactory(code="pmi-pool", pool=bahia)
    SupplementFactory(code="one-way", amount=Decimal("50.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, salida, dias=3, return_office=llegada)
    )

    assert resultado.supplements_total == Decimal("0.00")


def test_devolver_en_la_misma_oficina_nunca_es_one_way(categoria, palma, tarifa):
    SupplementFactory(code="one-way", amount=Decimal("50.00"))

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3))

    assert resultado.supplements_total == Decimal("0.00")


def test_dos_oficinas_sin_pool_son_sitios_distintos(categoria, palma, alcudia, tarifa):
    """`None == None` no significa "misma flota"."""
    SupplementFactory(code="one-way", amount=Decimal("50.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=3, return_office=alcudia)
    )

    assert resultado.supplements_total == Decimal("50.00")


def test_conductor_de_21_anos_paga_suplemento_joven(categoria, palma, tarifa):
    """Obligatorio."""
    SupplementFactory(
        code="joven",
        name="Conductor joven",
        supplement_type=SupplementType.YOUNG_DRIVER,
        amount=Decimal("15.00"),
        min_age=18,
        max_age=24,
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3, customer_age=21))

    assert resultado.supplements_total == Decimal("15.00")


def test_conductor_de_30_anos_no_paga_suplemento_joven(categoria, palma, tarifa):
    """Obligatorio."""
    SupplementFactory(
        code="joven",
        supplement_type=SupplementType.YOUNG_DRIVER,
        amount=Decimal("15.00"),
        min_age=18,
        max_age=24,
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3, customer_age=30))

    assert resultado.supplements_total == Decimal("0.00")


def test_sin_edad_conocida_no_se_cobra_el_joven(categoria, palma, tarifa):
    """Ante la duda no se cobra: cobrar de mas es peor que preguntar."""
    SupplementFactory(
        code="joven",
        supplement_type=SupplementType.YOUNG_DRIVER,
        amount=Decimal("15.00"),
        max_age=24,
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3))

    assert resultado.supplements_total == Decimal("0.00")


def test_el_suplemento_porcentual_va_sobre_la_base(categoria, palma, tarifa):
    """Es como se modela un cargo por dia: la base ya es proporcional."""
    SupplementFactory(
        code="joven",
        supplement_type=SupplementType.YOUNG_DRIVER,
        amount_type=AmountType.PERCENT,
        amount=Decimal("10.00"),
        max_age=24,
    )

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=3, customer_age=20))

    assert resultado.base_amount == Decimal("135.00")
    assert resultado.supplements_total == Decimal("13.50")


def test_suplemento_de_aeropuerto(categoria, palma, tarifa):
    aeropuerto = OfficeFactory(code="pmi-aero")
    SupplementFactory(
        code="aeropuerto",
        supplement_type=SupplementType.AIRPORT,
        amount=Decimal("22.00"),
        offices=[aeropuerto],
    )

    con = calculate_reservation_price(consulta(categoria, aeropuerto, dias=2))
    sin = calculate_reservation_price(consulta(categoria, palma, dias=2))

    assert con.supplements_total == Decimal("22.00")
    assert sin.supplements_total == Decimal("0.00")


def test_suplemento_fuera_de_horario(categoria, palma, tarifa):
    from datetime import time

    SupplementFactory(
        code="fuera-horario",
        supplement_type=SupplementType.AFTER_HOURS,
        amount=Decimal("35.00"),
        hours_from=time(8, 0),
        hours_to=time(20, 0),
    )

    de_noche = calculate_reservation_price(
        consulta(categoria, palma, pickup_at=cita(hora=23), devolucion=cita(dia=3, hora=12))
    )
    de_dia = calculate_reservation_price(
        consulta(categoria, palma, pickup_at=cita(hora=10), devolucion=cita(dia=3, hora=12))
    )

    assert de_noche.supplements_total == Decimal("35.00")
    assert de_dia.supplements_total == Decimal("0.00")


# ---------------------------------------------------------------------------
# Descuentos
# ---------------------------------------------------------------------------


def test_un_descuento_automatico_se_aplica_solo(categoria, palma, tarifa):
    DiscountFactory(name="Larga duracion", amount=Decimal("10.00"), min_days=7)

    corta = calculate_reservation_price(consulta(categoria, palma, dias=3))
    larga = calculate_reservation_price(consulta(categoria, palma, dias=10))

    assert corta.discounts_total == Decimal("0.00")
    assert larga.base_amount == Decimal("350.00")
    assert larga.discounts_total == Decimal("35.00")


def test_un_descuento_con_codigo_necesita_el_codigo(categoria, palma, tarifa):
    DiscountFactory(code="verano26", name="Verano", amount=Decimal("20.00"))

    sin_codigo = calculate_reservation_price(consulta(categoria, palma, dias=2))
    con_codigo = calculate_reservation_price(
        consulta(categoria, palma, dias=2, discount_code="VERANO26")
    )

    assert sin_codigo.discounts_total == Decimal("0.00")
    assert con_codigo.discounts_total == Decimal("18.00")  # 20% de 90


def test_un_codigo_que_no_vale_avisa_pero_no_rompe(categoria, palma, tarifa):
    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=2, discount_code="no-existe")
    )

    assert resultado.total > Decimal("0.00")
    assert any("no es valido" in aviso for aviso in resultado.warnings)


def test_un_descuento_fijo_no_deja_la_base_en_negativo(categoria, palma, tarifa):
    DiscountFactory(name="Bono", amount_type=AmountType.FIXED, amount=Decimal("500.00"))

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=1))

    assert resultado.discounts_total == Decimal("50.00")
    assert resultado.taxable_base == Decimal("0.00")


# ---------------------------------------------------------------------------
# Precio manual
# ---------------------------------------------------------------------------


def test_el_precio_manual_manda_y_queda_avisado(categoria, palma, tarifa):
    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=4, manual_override=Decimal("33.00"))
    )

    assert resultado.base_amount == Decimal("132.00")
    assert any("forzado a mano" in aviso for aviso in resultado.warnings)


# ---------------------------------------------------------------------------
# Impuestos y totales
# ---------------------------------------------------------------------------


def test_la_base_mas_el_iva_es_el_total(categoria, palma, tarifa):
    silla = crear_extra(max_amount=None)
    SupplementFactory(code="one-way", amount=Decimal("50.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=3, extras=(ExtraRequest(silla, 1),))
    )

    assert resultado.taxable_base + resultado.tax_total == resultado.total
    assert resultado.taxable_base == sum(linea.base for linea in resultado.lines)
    assert resultado.tax_total == sum(linea.tax_amount for linea in resultado.lines)


def test_cada_linea_cuadra_por_dentro(categoria, palma, tarifa):
    silla = crear_extra(max_amount=None)

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=5, extras=(ExtraRequest(silla, 1),))
    )

    for linea in resultado.lines:
        assert linea.base + linea.tax_amount == linea.total
        assert linea.base == linea.base.quantize(Decimal("0.01"))


def test_el_iva_es_configurable(categoria, palma, tarifa, settings):
    settings.DEFAULT_TAX_RATE = Decimal("10.00")

    resultado = calculate_reservation_price(consulta(categoria, palma, dias=2))

    assert resultado.taxable_base == Decimal("90.00")
    assert resultado.tax_total == Decimal("9.00")


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------


def test_el_desglose_es_serializable(categoria, palma, tarifa):
    """Criterio de aceptacion: se puede ensenar tal cual en la ficha."""
    silla = crear_extra(max_amount=Decimal("30.00"))
    SupplementFactory(code="one-way", amount=Decimal("50.00"))
    DiscountFactory(name="Promo", amount=Decimal("5.00"))

    resultado = calculate_reservation_price(
        consulta(categoria, palma, dias=6, extras=(ExtraRequest(silla, 1),))
    )
    como_json = json.dumps(resultado.to_dict())
    vuelta = json.loads(como_json)

    assert vuelta["rental_days"] == 6
    assert vuelta["applied_rate"] == "base"
    assert vuelta["total"] == str(resultado.total)
    assert len(vuelta["lines"]) == len(resultado.lines)
    assert all(isinstance(linea["base"], str) for linea in vuelta["lines"])


def test_el_desglose_trae_los_objetos_para_la_pantalla(categoria, palma, tarifa):
    resultado = calculate_reservation_price(consulta(categoria, palma, dias=5))

    assert resultado.applied_rate.name == "Tarifa base"
    assert resultado.applied_tier.price_per_day == Decimal("40.00")
    assert resultado.daily_price == Decimal("40.00")
