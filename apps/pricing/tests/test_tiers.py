"""Validacion de tramos. Funcion pura: ni base de datos ni formularios."""

from apps.pricing.services import TierSpec, coverage_summary, validate_tiers


def tramos(*definiciones):
    return [TierSpec(min_days=d, max_days=h, price_per_day="10.00") for d, h in definiciones]


def test_los_tramos_completos_no_dan_problemas():
    correctos = tramos((1, 1), (2, 3), (4, 7), (8, 14), (15, None))

    assert validate_tiers(correctos) == []


def test_un_solo_tramo_abierto_vale():
    assert validate_tiers(tramos((1, None))) == []


def test_un_hueco_en_el_dia_cuatro_se_rechaza():
    """Criterio de aceptacion: tramos 1-3 y 5-7 (falta el 4)."""
    problemas = validate_tiers(tramos((1, 3), (5, 7)))

    assert problemas
    assert any("dia 4" in problema for problema in problemas)


def test_un_hueco_de_varios_dias_lo_dice_entero():
    problemas = validate_tiers(tramos((1, 3), (8, None)))

    assert any("dias 4 a 7" in problema for problema in problemas)


def test_dos_tramos_que_se_solapan_se_rechazan():
    problemas = validate_tiers(tramos((1, 5), (4, 10), (11, None)))

    assert any("solapan" in problema for problema in problemas)


def test_no_empezar_en_el_dia_uno_se_rechaza():
    problemas = validate_tiers(tramos((2, 5), (6, None)))

    assert any("dia 1" in problema for problema in problemas)


def test_el_ultimo_tramo_tiene_que_quedar_abierto():
    """Si el ultimo cierra, un alquiler mas largo se queda sin precio."""
    problemas = validate_tiers(tramos((1, 3), (4, 7)))

    assert any("se quedaria sin precio" in problema for problema in problemas)


def test_solo_el_ultimo_puede_estar_abierto():
    problemas = validate_tiers(tramos((1, None), (2, None)))

    assert any("ultimo tramo" in problema for problema in problemas)


def test_un_tramo_abierto_en_medio_se_rechaza():
    problemas = validate_tiers(tramos((1, None), (5, 9)))

    assert problemas


def test_una_tarifa_sin_tramos_no_tiene_precio():
    problemas = validate_tiers([])

    assert any("sin tramos" in problema for problema in problemas)


def test_un_tramo_que_acaba_antes_de_empezar():
    problemas = validate_tiers(tramos((1, 3), (4, 2), (5, None)))

    assert any("acaba antes de empezar" in problema for problema in problemas)


def test_el_orden_en_el_que_se_teclean_da_igual():
    """El editor no obliga a meterlos ordenados."""
    desordenados = tramos((8, 14), (1, 1), (15, None), (2, 3), (4, 7))

    assert validate_tiers(desordenados) == []


def test_el_resumen_de_cobertura_es_legible():
    assert coverage_summary(tramos((1, 3), (4, 7), (8, None))) == "1-3, 4-7, 8+"
