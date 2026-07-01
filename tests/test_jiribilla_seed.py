from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "app" / "schemas" / "jiribilla"
CONTENT_ROOT = ROOT / "content" / "jiribilla"

EXPECTED_SECTIONS = [
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


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_jiribilla_expected_schema_and_content_files_exist():
    assert SCHEMA_ROOT.exists()
    assert CONTENT_ROOT.exists()

    schema_sections = sorted(p.name for p in SCHEMA_ROOT.iterdir() if p.is_dir())
    content_sections = sorted(p.stem.removesuffix("_v1") for p in CONTENT_ROOT.glob("*_v1.json"))

    assert schema_sections == sorted(EXPECTED_SECTIONS)
    assert content_sections == sorted(EXPECTED_SECTIONS)


def test_jiribilla_seed_content_validates_against_schemas():
    for section in EXPECTED_SECTIONS:
        schema_path = SCHEMA_ROOT / section / "v1.json"
        content_path = CONTENT_ROOT / f"{section}_v1.json"

        schema = _load(schema_path)
        content = _load(content_path)

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(content)


def test_jiribilla_pdf_constraints_are_encoded():
    proyectos = _load(SCHEMA_ROOT / "proyectos" / "v1.json")
    project_awards = proyectos["$defs"]["Project"]["properties"]["projectAwards"]
    assert project_awards["maxItems"] == 3

    equipo = _load(SCHEMA_ROOT / "equipo" / "v1.json")
    assert equipo["properties"]["bottomText"]["maxLength"] == 40

    footer = _load(SCHEMA_ROOT / "footer" / "v1.json")
    assert footer["properties"]["footerPhrase"]["maxLength"] == 40
