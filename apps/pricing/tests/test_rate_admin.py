"""Pantallas de mantenimiento de tarifas: editor, simulador, conflictos y copia."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fleet.tests.factories import VehicleCategoryFactory
from apps.pricing.models import Rate, RateTier, Season, TierMode
from apps.pricing.services import find_rate_conflicts

from .factories import TRAMOS_ESTANDAR, RateFactory, SeasonFactory

pytestmark = pytest.mark.django_db

HTMX = {"hx-request": "true"}


@pytest.fixture
def categoria():
    return VehicleCategoryFactory(code="eco", name="Economico")


def datos_tarifa(categoria, tramos, **cambios):
    """POST completo de una tarifa con su formset de tramos."""
    datos = {
        "code": "eco-baja",
        "name": "Economico baja",
        "channel": "counter",
        "categories": [categoria.pk],
        "priority": 0,
        "tier_mode": TierMode.FLAT,
        "extra_km_price": "0.00",
        "min_days": 1,
        "tiers-TOTAL_FORMS": str(len(tramos)),
        "tiers-INITIAL_FORMS": "0",
        "tiers-MIN_NUM_FORMS": "0",
        "tiers-MAX_NUM_FORMS": "1000",
    }
    for indice, (desde, hasta, precio) in enumerate(tramos):
        datos[f"tiers-{indice}-min_days"] = str(desde)
        datos[f"tiers-{indice}-max_days"] = "" if hasta is None else str(hasta)
        datos[f"tiers-{indice}-price_per_day"] = precio
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Alta de tarifa con tramos
# ---------------------------------------------------------------------------


def test_el_alta_crea_la_tarifa_con_sus_tramos(client, gestor_tarifas, categoria):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:rate_create"),
        datos_tarifa(categoria, [(1, 3, "50.00"), (4, None, "40.00")]),
        headers=HTMX,
    )
    tarifa = Rate.objects.get(code="eco-baja")

    assert respuesta.status_code == 200
    assert tarifa.tiers.count() == 2
    assert list(tarifa.categories.all()) == [categoria]


def test_un_hueco_en_el_dia_cuatro_no_se_guarda(client, gestor_tarifas, categoria):
    """Criterio de aceptacion: tramos 1-3 y 5-7 se rechazan al guardar."""
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:rate_create"),
        datos_tarifa(categoria, [(1, 3, "50.00"), (5, 7, "40.00")]),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "dia 4" in respuesta.content.decode()
    assert not Rate.objects.filter(code="eco-baja").exists()
    assert RateTier.objects.count() == 0  # no se guarda nada a medias


def test_unos_tramos_que_se_solapan_no_se_guardan(client, gestor_tarifas, categoria):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:rate_create"),
        datos_tarifa(categoria, [(1, 5, "50.00"), (4, None, "40.00")]),
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "solapan" in respuesta.content.decode()
    assert not Rate.objects.filter(code="eco-baja").exists()


def test_una_tarifa_sin_tramos_no_se_guarda(client, gestor_tarifas, categoria):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:rate_create"), datos_tarifa(categoria, []), headers=HTMX
    )

    assert respuesta.status_code == 422
    assert not Rate.objects.filter(code="eco-baja").exists()


def test_editar_los_tramos_cambia_el_precio(client, gestor_tarifas, categoria):
    """Criterio del prompt anterior, ahora desde la pantalla."""
    tarifa = RateFactory(code="eco-baja", categories=[categoria], tiers=[(1, None, "50.00")])
    tramo = tarifa.tiers.first()
    client.force_login(gestor_tarifas)

    client.post(
        reverse("pricing:rate_update", args=[tarifa.pk]),
        datos_tarifa(
            categoria,
            [],
            **{
                "tiers-TOTAL_FORMS": "1",
                "tiers-INITIAL_FORMS": "1",
                "tiers-0-id": str(tramo.pk),
                "tiers-0-rate": str(tarifa.pk),
                "tiers-0-min_days": "1",
                "tiers-0-max_days": "",
                "tiers-0-price_per_day": "77.00",
            },
        ),
        headers=HTMX,
    )
    tramo.refresh_from_db()

    assert tramo.price_per_day == Decimal("77.00")


# ---------------------------------------------------------------------------
# Aviso en vivo del editor
# ---------------------------------------------------------------------------


def test_el_aviso_en_vivo_detecta_el_hueco(client, gestor_tarifas):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:tier_check"),
        {
            "tiers-TOTAL_FORMS": "2",
            "tiers-0-min_days": "1",
            "tiers-0-max_days": "3",
            "tiers-0-price_per_day": "50.00",
            "tiers-1-min_days": "5",
            "tiers-1-max_days": "7",
            "tiers-1-price_per_day": "40.00",
        },
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert "dia 4" in respuesta.content.decode()


def test_el_aviso_en_vivo_confirma_la_cobertura(client, gestor_tarifas):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:tier_check"),
        {
            "tiers-TOTAL_FORMS": "2",
            "tiers-0-min_days": "1",
            "tiers-0-max_days": "3",
            "tiers-0-price_per_day": "50.00",
            "tiers-1-min_days": "4",
            "tiers-1-max_days": "",
            "tiers-1-price_per_day": "40.00",
        },
        headers=HTMX,
    )
    contenido = respuesta.content.decode()

    assert "Cobertura correcta" in contenido
    assert "1-3, 4+" in contenido


def test_el_aviso_en_vivo_ignora_las_filas_vacias(client, gestor_tarifas):
    """Mientras se teclea hay filas a medias: no se avisa de lo que aun no es."""
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:tier_check"),
        {
            "tiers-TOTAL_FORMS": "2",
            "tiers-0-min_days": "1",
            "tiers-0-max_days": "",
            "tiers-0-price_per_day": "50.00",
            "tiers-1-min_days": "",
            "tiers-1-max_days": "",
            "tiers-1-price_per_day": "",
        },
        headers=HTMX,
    )

    assert "Cobertura correcta" in respuesta.content.decode()


# ---------------------------------------------------------------------------
# Duplicar
# ---------------------------------------------------------------------------


def test_duplicar_copia_tramos_categorias_y_oficinas(client, gestor_tarifas, categoria, centro):
    """Es como se monta la temporada alta a partir de la baja."""
    baja = SeasonFactory(code="baja", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
    original = RateFactory(
        code="eco-baja",
        name="Economico baja",
        categories=[categoria],
        offices=[centro],
        season=baja,
        tiers=TRAMOS_ESTANDAR,
    )
    client.force_login(gestor_tarifas)

    respuesta = client.post(reverse("pricing:rate_duplicate", args=[original.pk]), headers=HTMX)
    copia = Rate.objects.get(code="eco-baja-copia")

    assert respuesta.status_code == 200
    assert copia.tiers.count() == original.tiers.count()
    assert list(copia.categories.all()) == [categoria]
    assert list(copia.offices.all()) == [centro]
    assert copia.season == baja


def test_la_copia_nace_desactivada(client, gestor_tarifas, categoria):
    """Nadie vende con una tarifa a medio repasar."""
    original = RateFactory(code="eco-baja", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    client.post(reverse("pricing:rate_duplicate", args=[original.pk]), headers=HTMX)

    assert Rate.objects.get(code="eco-baja-copia").is_active is False


def test_duplicar_dos_veces_no_choca_de_codigo(client, gestor_tarifas, categoria):
    original = RateFactory(code="eco-baja", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    client.post(reverse("pricing:rate_duplicate", args=[original.pk]), headers=HTMX)
    client.post(reverse("pricing:rate_duplicate", args=[original.pk]), headers=HTMX)

    assert Rate.objects.filter(code__startswith="eco-baja-copia").count() == 2


def test_duplicar_abre_la_copia_para_ajustarla(client, gestor_tarifas, categoria):
    original = RateFactory(code="eco-baja", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    respuesta = client.post(reverse("pricing:rate_duplicate", args=[original.pk]), headers=HTMX)
    copia = Rate.objects.get(code="eco-baja-copia")

    assert "crud:abrir-modal" in respuesta.headers["HX-Trigger"]
    assert reverse("pricing:rate_update", args=[copia.pk]) in respuesta.headers["HX-Trigger"]


# ---------------------------------------------------------------------------
# Simulador
# ---------------------------------------------------------------------------


def datos_simulador(categoria, oficina, **cambios):
    salida = timezone.localtime(timezone.make_aware(datetime(2026, 6, 1, 10, 0)))
    datos = {
        "category": categoria.pk,
        "pickup_office": oficina.pk,
        "return_office": oficina.pk,
        "pickup_at": salida.strftime("%Y-%m-%dT%H:%M"),
        "return_at": (salida + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
        "channel": "counter",
    }
    datos.update(cambios)
    return datos


def test_el_simulador_dice_que_tarifa_y_que_tramo_se_aplican(
    client, gestor_tarifas, categoria, centro
):
    """Criterio de aceptacion: no solo el total."""
    RateFactory(code="base", name="Tarifa base", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:simulator"), datos_simulador(categoria, centro), headers=HTMX
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Tarifa base" in contenido  # que tarifa
    assert "base" in contenido
    assert "4-7 dias" in contenido  # que tramo
    assert "200,00" in contenido or "200.00" in contenido  # 5 dias x 40


def test_el_simulador_ensena_el_desglose_por_lineas(client, gestor_tarifas, categoria, centro):
    from apps.pricing.models import CalculationType, Extra

    RateFactory(code="base", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    silla = Extra.objects.create(
        code="silla",
        name="Silla de bebe",
        calculation_type=CalculationType.PER_DAY,
        price=Decimal("5.00"),
        max_quantity=2,
    )
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:simulator"),
        datos_simulador(categoria, centro, extras=[silla.pk]),
        headers=HTMX,
    )
    contenido = respuesta.content.decode()

    assert "Silla de bebe" in contenido
    assert "Base imponible" in contenido


def test_el_simulador_ensena_el_error_en_vez_de_reventar(client, gestor_tarifas, categoria, centro):
    """Sin tarifa configurada, el administrador tiene que ver justo eso."""
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:simulator"), datos_simulador(categoria, centro), headers=HTMX
    )
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "NoRateAvailable" in contenido
    assert "No hay tarifa" in contenido


def test_el_simulador_no_crea_nada(client, gestor_tarifas, categoria, centro):
    from apps.customers.models import Customer

    RateFactory(code="base", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    client.post(reverse("pricing:simulator"), datos_simulador(categoria, centro), headers=HTMX)

    assert Customer.objects.count() == 0
    assert Rate.objects.count() == 1


# ---------------------------------------------------------------------------
# Conflictos
# ---------------------------------------------------------------------------


def test_dos_tarifas_que_empatan_salen_como_conflicto(client, gestor_tarifas, categoria):
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    conflictos = find_rate_conflicts()
    contenido = client.get(reverse("pricing:rate_conflicts")).content.decode()

    assert len(conflictos) == 1
    assert "una" in contenido and "otra" in contenido


def test_distinta_prioridad_no_es_conflicto(categoria):
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR, priority=0)
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR, priority=5)

    assert find_rate_conflicts() == []


def test_distinta_categoria_no_es_conflicto(categoria):
    otra_categoria = VehicleCategoryFactory(code="suv")
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    RateFactory(code="otra", categories=[otra_categoria], tiers=TRAMOS_ESTANDAR)

    assert find_rate_conflicts() == []


def test_distinto_canal_no_es_conflicto(categoria):
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR, channel="counter")
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR, channel="web")

    assert find_rate_conflicts() == []


def test_distinta_temporada_no_es_conflicto(categoria):
    una = SeasonFactory(code="baja", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
    otra = SeasonFactory(code="alta", start_date=date(2026, 7, 1), end_date=date(2026, 9, 30))
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR, season=una)
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR, season=otra)

    assert find_rate_conflicts() == []


def test_oficinas_distintas_no_es_conflicto(categoria, centro, norte):
    RateFactory(code="una", categories=[categoria], offices=[centro], tiers=TRAMOS_ESTANDAR)
    RateFactory(code="otra", categories=[categoria], offices=[norte], tiers=TRAMOS_ESTANDAR)

    assert find_rate_conflicts() == []


def test_una_tarifa_desactivada_no_entra_en_conflictos(categoria):
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    RateFactory(code="otra", categories=[categoria], tiers=TRAMOS_ESTANDAR, is_active=False)

    assert find_rate_conflicts() == []


def test_sin_conflictos_la_pantalla_lo_dice(client, gestor_tarifas, categoria):
    RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    contenido = client.get(reverse("pricing:rate_conflicts")).content.decode()

    assert "Ninguna tarifa se pisa" in contenido


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("pricing:rate_list", "get", False),
    ("pricing:rate_create", "get", False),
    ("pricing:rate_create", "post", False),
    ("pricing:rate_update", "get", True),
    ("pricing:rate_duplicate", "post", True),
    ("pricing:rate_activate", "post", True),
    ("pricing:season_list", "get", False),
    ("pricing:season_create", "post", False),
    ("pricing:supplement_list", "get", False),
    ("pricing:discount_list", "get", False),
    ("pricing:simulator", "get", False),
    ("pricing:rate_conflicts", "get", False),
    ("pricing:tier_check", "post", False),
]


@pytest.mark.parametrize(("vista", "metodo", "con_objeto"), ENDPOINTS)
def test_sin_permiso_403(client, agente_centro, categoria, vista, metodo, con_objeto):
    tarifa = RateFactory(code="una", categories=[categoria], tiers=TRAMOS_ESTANDAR)
    client.force_login(agente_centro)

    url = reverse(vista, args=[tarifa.pk] if con_objeto else [])

    assert getattr(client, metodo)(url, {}).status_code == 403


def test_el_responsable_ve_las_tarifas_pero_no_las_toca(client, centro, rol_mostrador):
    """Explicar un precio si; cambiarlo, no."""
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    responsable = UserFactory(
        email="responsable@ejemplo.es",
        role=RoleFactory(code="resp-test", permissions=["pricing.view_rate"]),
        offices=[centro],
    )
    client.force_login(responsable)

    assert client.get(reverse("pricing:rate_list")).status_code == 200
    assert client.get(reverse("pricing:simulator")).status_code == 200
    assert client.get(reverse("pricing:rate_create"), headers=HTMX).status_code == 403


def test_el_boton_de_alta_no_aparece_sin_permiso(client, centro, categoria):
    from apps.accounts.tests.factories import RoleFactory, UserFactory

    solo_lectura = UserFactory(
        email="lectura@ejemplo.es",
        role=RoleFactory(code="solo-ver-tarifas", permissions=["pricing.view_rate"]),
        offices=[centro],
    )
    client.force_login(solo_lectura)

    contenido = client.get(reverse("pricing:rate_list")).content.decode()

    assert "Nueva tarifa" not in contenido


# ---------------------------------------------------------------------------
# Temporadas
# ---------------------------------------------------------------------------


def test_el_alta_de_temporada(client, gestor_tarifas):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:season_create"),
        {
            "code": "ALTA",
            "name": "Temporada alta",
            "start_date": "2026-07-01",
            "end_date": "2026-09-30",
            "priority": 5,
        },
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    assert Season.objects.get(code="alta").priority == 5


def test_una_temporada_al_reves_no_se_guarda(client, gestor_tarifas):
    client.force_login(gestor_tarifas)

    respuesta = client.post(
        reverse("pricing:season_create"),
        {
            "code": "alta",
            "name": "Temporada alta",
            "start_date": "2026-09-30",
            "end_date": "2026-07-01",
            "priority": 0,
        },
        headers=HTMX,
    )

    assert respuesta.status_code == 422
    assert "acabar antes de empezar" in respuesta.content.decode()


def test_no_se_desactiva_una_temporada_con_tarifas_activas(client, gestor_tarifas, categoria):
    temporada = SeasonFactory(code="alta", start_date=date(2026, 7, 1), end_date=date(2026, 9, 30))
    RateFactory(code="alta-rate", categories=[categoria], season=temporada, tiers=TRAMOS_ESTANDAR)
    client.force_login(gestor_tarifas)

    respuesta = client.post(reverse("pricing:season_deactivate", args=[temporada.pk]), headers=HTMX)
    temporada.refresh_from_db()

    assert "tarifas activas" in respuesta.headers["HX-Trigger"]
    assert temporada.is_active is True
