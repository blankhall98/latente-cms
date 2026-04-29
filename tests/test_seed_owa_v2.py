from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v1.json")
CONTENT_PATH = Path("content/owa/home_v1.json")


def test_owa_landing_schema_and_seed_content_validate():
    assert SCHEMA_PATH.exists(), "OWA LandingPages v1 schema file is missing."
    assert CONTENT_PATH.exists(), "OWA home v1 content fixture is missing."

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    content = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(content)
