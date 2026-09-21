"""PDF de la factura.

Se genera al pedirlo y no se guarda: la factura es inmutable y todo lo que sale
impreso esta copiado en ella al emitirse, asi que el PDF de hoy y el de dentro
de un ano dicen exactamente lo mismo. Guardar el fichero seria tener dos
versiones de la misma verdad.
"""

from django.template.loader import render_to_string

from .verifactu import qr_svg


def invoice_context(invoice) -> dict:
    """Todo lo que sale impreso, sacado de la propia factura."""
    from apps.settings_app.models import CompanySettings

    empresa = CompanySettings.load()
    return {
        "invoice": invoice,
        "lineas": list(invoice.lines.all()),
        "qr": qr_svg(invoice.qr_data),
        # Solo lo decorativo sale de la configuracion actual; los datos
        # fiscales son los que la factura copio al emitirse.
        "logo_path": empresa.logo.path if empresa.logo else "",
        "nota_registral": empresa.registry_note,
    }


def document_language(customer) -> str:
    """Idioma de los documentos del cliente. Sin cliente, el de la instalacion."""
    from django.conf import settings

    idioma = getattr(customer, "language", "") or settings.LANGUAGE_CODE
    return idioma if idioma in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE


def render_invoice_pdf(invoice) -> bytes:
    """La factura en el idioma del cliente: es un documento para el."""
    from django.utils import translation
    from weasyprint import HTML

    with translation.override(document_language(invoice.customer)):
        html = render_to_string("billing/invoice_pdf.html", invoice_context(invoice))
    return HTML(string=html, base_url=".").write_pdf()
