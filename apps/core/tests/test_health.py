import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_ok_cuando_bd_y_redis_responden(client):
    response = client.get(reverse("health"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["redis"]["ok"] is True


@pytest.mark.django_db
def test_health_devuelve_503_si_redis_no_responde(client, monkeypatch):
    monkeypatch.setattr(
        "apps.core.views._check_redis",
        lambda: (False, "Connection refused"),
    )

    response = client.get(reverse("health"))

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["redis"]["ok"] is False
