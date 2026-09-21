"""El comando que carga la demostracion."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_cargada(settings):
    settings.DEBUG = True
    call_command("seed_demo", verbosity=0)


def test_la_demo_monta_una_empresa_entera(demo_cargada):
    from apps.customers.models import Customer
    from apps.fleet.models import Vehicle, VehicleCategory
    from apps.offices.models import Office
    from apps.reservations.models import Reservation
    from apps.settings_app.models import CompanySettings, TermsVersion

    assert Office.objects.count() == 3
    assert VehicleCategory.objects.count() == 5
    assert Vehicle.objects.count() > 20
    assert Customer.objects.count() == 12
    assert Reservation.objects.count() > 10
    assert CompanySettings.load().legal_name.startswith("Autos Demo")
    assert TermsVersion.current() is not None


def test_la_demo_tiene_actividad_de_verdad(demo_cargada):
    """No son filas sueltas: hay reservas en todos los estados del ciclo."""
    from apps.operations.models import CheckIn, CheckOut
    from apps.reservations.models import Reservation, ReservationStatus

    estados = set(Reservation.objects.values_list("status", flat=True))

    assert ReservationStatus.FINISHED in estados
    assert ReservationStatus.IN_PROGRESS in estados
    assert ReservationStatus.CONFIRMED in estados
    assert CheckIn.objects.exists()
    assert CheckOut.objects.exists()


def test_la_demo_tiene_cobros_y_fianzas(demo_cargada):
    from apps.billing.models import Payment, PaymentType

    tipos = set(Payment.objects.values_list("payment_type", flat=True))

    assert Payment.objects.exists()
    assert PaymentType.DEPOSIT in tipos


def test_el_usuario_de_demo_puede_entrar(demo_cargada, settings, client):
    settings.AXES_ENABLED = False

    respuesta = client.post(
        "/entrar/", {"username": settings.DEMO_EMAIL, "password": settings.DEMO_PASSWORD}
    )

    assert respuesta.status_code == 302
    assert respuesta.wsgi_request.user.is_authenticated


def test_se_puede_lanzar_dos_veces_sin_duplicar(demo_cargada, settings):
    from apps.customers.models import Customer
    from apps.offices.models import Office

    settings.DEBUG = True
    call_command("seed_demo", verbosity=0)

    assert Office.objects.count() == 3
    assert Customer.objects.count() == 12


def test_en_produccion_no_se_cargan_datos_de_mentira(settings):
    settings.DEBUG = False
    settings.DEMO_MODE = False

    with pytest.raises(CommandError) as fallo:
        call_command("seed_demo", verbosity=0)

    assert "produccion" in str(fallo.value)


def test_la_cuenta_demo_ve_todas_las_opciones_del_menu(demo_cargada, settings):
    """En la demo se tiene que poder abrir todo: simulador, facturacion, series..."""
    from apps.accounts.models import User
    from apps.core.navigation import MAIN_NAV, sections_for

    demo = User.objects.get(email=settings.DEMO_EMAIL)
    todas = {item.url_name for seccion in MAIN_NAV for item in seccion.items}
    visibles = {item.url_name for seccion in sections_for(demo) for item in seccion.items}

    assert visibles == todas


def test_los_permisos_de_la_demo_no_cambian_el_rol_de_mostrador(demo_cargada, settings):
    """Van a la cuenta demo, no al rol: un mostrador de verdad sigue igual."""
    from apps.accounts.models import Role

    mostrador = Role.objects.get(code="mostrador")

    assert not mostrador.permissions.filter(codename="view_invoiceseries").exists()
    assert not mostrador.permissions.filter(codename="view_rate").exists()


def test_la_demo_trae_facturas_y_deja_algo_por_facturar(demo_cargada):
    from apps.accounts.models import User
    from apps.billing.models import Invoice
    from apps.billing.selectors import reservations_to_invoice

    direccion = User.objects.get(email="direccion@autosdemo.example")

    assert Invoice.objects.exists()
    assert reservations_to_invoice(direccion).exists()


def test_el_boton_de_demo_solo_sale_con_demo_mode(client, settings):
    settings.DEMO_MODE = True
    assert "Entrar en la demostración" in client.get("/entrar/").content.decode()

    settings.DEMO_MODE = False
    assert "Entrar en la demostración" not in client.get("/entrar/").content.decode()
