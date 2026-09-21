"""Tareas periodicas de facturacion."""

from celery import shared_task


@shared_task
def expire_payment_links():
    from .online import expire_old_links

    return expire_old_links()
