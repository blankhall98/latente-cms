# tests/test_delivery_optimizations.py
from __future__ import annotations

import pytest
from typing import Set
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)


def _get_etag(resp) -> str | None:
    return resp.headers.get("ETag")


def _only_requested_keys(d: dict, requested: Set[str]) -> bool:
    # Puede faltar alguna key solicitada (si no existe en data),
    # pero no deben aparecer extras fuera de lo pedido.
    return set(d.keys()).issubset(requested)


def test_list_projection_and_filter():
    # Lista base
    r0 = client.get("/delivery/v1/entries", params={
        "tenant_slug": "latente",
        "section_key": "LandingPages",
        "limit": 10,
        "offset": 0,
    })
    assert r0.status_code == 200, r0.text
    payload0 = r0.json()
    assert "items" in payload0
    items0 = payload0["items"]

    # Proyección: solo ciertas claves dentro de data (funciona incluso con lista vacía)
    r1 = client.get("/delivery/v1/entries", params={
        "tenant_slug": "latente",
        "section_key": "LandingPages",
        "limit": 10,
        "offset": 0,
        "fields": "title,heroImage",
    })
    assert r1.status_code == 200, r1.text
    payload1 = r1.json()
    for it in payload1["items"]:
        data = it.get("data") or {}
        assert isinstance(data, dict)
        assert _only_requested_keys(data, {"title", "heroImage"})

    # Filtro por data__nonexistent -> 0 elementos
    r2 = client.get("/delivery/v1/entries", params={
        "tenant_slug": "latente",
        "section_key": "LandingPages",
        "limit": 10,
        "offset": 0,
        "data__nonexistent": "xyz",
    })
    assert r2.status_code == 200, r2.text
    payload2 = r2.json()
    assert isinstance(payload2.get("items"), list)
    assert len(payload2["items"]) == 0


def test_list_etag_and_304():
    # ETag base
    r_base = client.get("/delivery/v1/entries", params={
        "tenant_slug": "latente",
        "section_key": "LandingPages",
        "limit": 10,
        "offset": 0,
    })
    assert r_base.status_code == 200, r_base.text
    etag_base = _get_etag(r_base)
    assert etag_base, "ETag must be present on list"

    # If-None-Match -> 304 en lista
    r_304 = client.get(
        "/delivery/v1/entries",
        params={
            "tenant_slug": "latente",
            "section_key": "LandingPages",
            "limit": 10,
            "offset": 0,
        },
        headers={"If-None-Match": etag_base},
    )
    assert r_304.status_code == 304, r_304.text


def test_detail_projection_and_caching():
    # Intentamos obtener un detalle conocido ('home'); si no existe publicado aún,
    # saltamos la prueba para no acoplarla al orden de ejecución.
    r0 = client.get("/delivery/v1/tenants/latente/sections/LandingPages/entries/home")
    if r0.status_code == 404:
        pytest.skip("No published 'home' entry available; skipping detail test.")

    assert r0.status_code == 200, r0.text
    etag0 = _get_etag(r0)
    assert etag0, "ETag must be present on detail"

    # Detalle proyectado
    r1 = client.get(
        "/delivery/v1/tenants/latente/sections/LandingPages/entries/home",
        params={"fields": "title,heroImage"},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    data1 = body1.get("data") or {}
    assert isinstance(data1, dict)
    assert _only_requested_keys(data1, {"title", "heroImage"})
    etag1 = _get_etag(r1)
    # ETag puede o no cambiar según el contenido real; comprobamos 304
    r304 = client.get(
        "/delivery/v1/tenants/latente/sections/LandingPages/entries/home",
        params={"fields": "title,heroImage"},
        headers={"If-None-Match": etag1},
    )
    assert r304.status_code == 304, r304.text

