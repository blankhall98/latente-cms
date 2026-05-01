from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


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

    section_def = schema["$defs"]["LegalSection"]
    assert section_def["x-ui"]["order"] == ["sectionTitle", "sectionText"]

    long_text = schema["$defs"]["LocalizedLongTextarea"]["properties"]
    assert set(long_text.keys()) == {"en", "es"}
    assert long_text["en"]["x-ui"]["textarea"] is True
    assert long_text["es"]["x-ui"]["textarea"] is True
    assert long_text["en"]["maxLength"] >= 10000
    assert long_text["es"]["maxLength"] >= 10000
