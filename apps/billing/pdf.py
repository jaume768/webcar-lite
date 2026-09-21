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


def render_invoice_pdf(invoice) -> bytes:
    from weasyprint import HTML

    html = render_to_string("billing/invoice_pdf.html", invoice_context(invoice))
    return HTML(string=html, base_url=".").write_pdf()
