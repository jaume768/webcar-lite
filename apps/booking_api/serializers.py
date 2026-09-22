"""De objetos del dominio a JSON. Importes como cadena, fechas en ISO 8601."""

from django.conf import settings
from django.urls import reverse
from django.utils import timezone


def _momento(valor):
    return timezone.localtime(valor).isoformat() if valor else None


def office(oficina) -> dict:
    return {
        "code": oficina.code,
        "name": oficina.name,
        "address": oficina.address,
        "city": oficina.city,
        "province": oficina.province,
        "postal_code": oficina.postal_code,
        "country": oficina.country,
        "phone": oficina.phone,
        "email": oficina.email,
    }


def category(categoria) -> dict:
    return {
        "code": categoria.code,
        "name": categoria.name,
        "description": categoria.description,
        "image_url": (settings.PUBLIC_BASE_URL + categoria.image.url) if categoria.image else None,
        "seats": categoria.seats,
        "doors": categoria.doors,
        "luggage": categoria.luggage,
        "transmission": categoria.transmission,
        "fuel": categoria.fuel,
        "air_conditioning": categoria.air_conditioning,
    }


def extra(extra) -> dict:
    return {
        "code": extra.code,
        "name": extra.name,
        "description": extra.description,
        "calculation_type": extra.calculation_type,
        "price": str(extra.price),
        "tax_rate": str(extra.tax_rate),
        "max_quantity": extra.max_quantity,
    }


def breakdown(desglose) -> dict:
    return {
        "rental_days": desglose.rental_days,
        "currency": desglose.currency,
        "base_amount": str(desglose.base_amount),
        "extras_total": str(desglose.extras_total),
        "supplements_total": str(desglose.supplements_total),
        "discounts_total": str(desglose.discounts_total),
        "taxable_base": str(desglose.taxable_base),
        "tax_total": str(desglose.tax_total),
        "total": str(desglose.total),
        "lines": [
            {
                "kind": linea.kind,
                "concept": linea.concept,
                "quantity": str(linea.quantity),
                "unit_price": str(linea.unit_price),
                "base": str(linea.base),
                "tax_rate": str(linea.tax_rate),
                "tax_amount": str(linea.tax_amount),
                "total": str(linea.total),
            }
            for linea in desglose.lines
        ],
    }


def offer(oferta) -> dict:
    return {
        "category": category(oferta.category),
        "available_units": oferta.free,
        "price": breakdown(oferta.breakdown),
    }


def payment(pago) -> dict:
    return {
        "provider": pago.provider,
        "purpose": pago.purpose,
        "amount": str(pago.amount),
        "status": pago.status,
        "url": settings.PUBLIC_BASE_URL + reverse("billing:pay", args=[pago.token]),
        "expires_at": _momento(pago.expires_at),
    }


def reservation(reserva, *, pagos=()) -> dict:
    from apps.billing.selectors import paid_amount, pending_amount

    guardado = reserva.price_breakdown or {}
    return {
        "number": reserva.number,
        "status": reserva.status,
        "status_label": str(reserva.get_status_display()),
        "category": reserva.category.code,
        "pickup_office": reserva.pickup_office.code,
        "return_office": reserva.return_office.code,
        "pickup_at": _momento(reserva.pickup_at),
        "return_at": _momento(reserva.return_at),
        "customer": {
            "first_name": reserva.customer.first_name,
            "last_name": reserva.customer.last_name,
            "email": reserva.customer.email,
        }
        if reserva.customer_id
        else None,
        "extras": [
            {"code": linea.extra.code, "quantity": linea.quantity, "total": str(linea.total)}
            for linea in reserva.extras.select_related("extra")
        ],
        "price": {
            "rental_days": guardado.get("rental_days"),
            "currency": guardado.get("currency", "EUR"),
            "base_amount": str(reserva.base_amount),
            "extras_total": str(reserva.extras_total),
            "supplements_total": str(reserva.supplements_total),
            "discounts_total": str(reserva.discounts_total),
            "tax_total": str(reserva.tax_total),
            "total": str(reserva.total),
            "lines": guardado.get("lines", []),
        },
        "deposit_amount": str(reserva.deposit_amount),
        "paid_amount": str(paid_amount(reserva)),
        "pending_amount": str(pending_amount(reserva)),
        "cancellation_fee": str(reserva.cancellation_fee),
        "payments": [payment(pago) for pago in pagos],
    }
