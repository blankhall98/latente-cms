from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.services.ui_schema_service import build_sections_ui_fallback_for_object_page


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "app" / "schemas" / "dewa" / "legals" / "v1.json"
CONTENT_PATH = ROOT / "content" / "dewa" / "legals_v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dewa_legals_seed_matches_schema():
    schema = _load(SCHEMA_PATH)
    content = _load(CONTENT_PATH)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(content)


def test_dewa_legals_has_three_localized_editable_sections():
    schema = _load(SCHEMA_PATH)

    assert schema["x-ui"]["order"] == [
        "legalSectionOne",
        "legalSectionTwo",
        "legalSectionThree",
    ]
    for key in schema["x-ui"]["order"]:
        assert schema["properties"][key]["x-ui"]["order"] == ["sectionTitle", "sectionText"]

    section_def = schema["$defs"]["LegalSection"]
    assert section_def["x-ui"]["order"] == ["sectionTitle", "sectionText"]

    long_text = schema["$defs"]["LocalizedLongTextarea"]["properties"]
    assert set(long_text.keys()) == {"en", "es"}
    assert long_text["en"]["x-ui"]["textarea"] is True
    assert long_text["es"]["x-ui"]["textarea"] is True
    assert long_text["en"]["maxLength"] >= 10000
    assert long_text["es"]["maxLength"] >= 10000


def test_dewa_legals_editor_labels_use_english_section_title():
    schema = _load(SCHEMA_PATH)
    content = _load(CONTENT_PATH)
    content["legalSectionOne"]["sectionTitle"]["en"] = "Privacy Policy"
    content["legalSectionTwo"]["sectionTitle"]["en"] = "Terms of Use"

    sections = build_sections_ui_fallback_for_object_page(content, schema)

    assert sections[0]["label"] == "01 - Privacy Policy"
    assert sections[1]["label"] == "02 - Terms of Use"
    assert sections[2]["label"] == "03 - Section 3"
