from __future__ import annotations

from pathlib import Path

from scripts import bootstrap_jiribilla


def test_jiribilla_bootstrap_constants():
    assert bootstrap_jiribilla.TENANT_NAME == "Jiribilla"
    assert bootstrap_jiribilla.TENANT_SLUG == "jiribilla"
    assert bootstrap_jiribilla.CONTACT_EMAIL == "hola@jiribilla.studio"
    assert bootstrap_jiribilla.SECTIONS == [
        "hero",
        "mesa_uno",
        "proyectos",
        "eventos_privados",
        "glosario",
        "equipo",
        "footer",
        "social_links",
        "forms",
        "privacy_policy",
    ]
    assert bootstrap_jiribilla.SECTION_LABELS["social_links"] == "Social and Links"


def test_jiribilla_bootstrap_content_paths_exist():
    for section in bootstrap_jiribilla.SECTIONS:
        assert Path(f"content/jiribilla/{section}_v1.json").exists()
