# Jiribilla Tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Jiribilla tenant with isolated schemas, default content, bootstrap support, and validation tests.

**Architecture:** Jiribilla is added as a tenant-owned content package using the repository's existing `app/schemas/<tenant>/<section>/vX.json` and `content/<tenant>/<section>_vX.json` patterns. A tenant-specific bootstrap script composes existing idempotent seed helpers instead of changing broad reset scripts. The only shared code change is a slug-keyed admin section order branch for `jiribilla`.

**Tech Stack:** Python, FastAPI, SQLAlchemy, JSON Schema draft 2020-12, `jsonschema`, pytest.

## Global Constraints

- Tenant name is `Jiribilla`.
- Tenant slug is `jiribilla`.
- Tenant/contact email is `hola@jiribilla.studio`.
- Do not edit existing ANRO, DEWA, OWA, or Ragni schema/content files.
- Do not create a passworded Jiribilla admin user.
- Use JSON Schema 2020-12 and existing `x-ui` conventions.
- Use `maxItems: 3` for project awards.
- Use `maxLength: 40` for `equipo.bottomText` and `footer.footerPhrase`.

---

### Task 1: Add Failing Fixture Validation Tests

**Files:**
- Create: `tests/test_jiribilla_seed.py`

**Interfaces:**
- Consumes: `app/schemas/jiribilla/<section>/v1.json` and `content/jiribilla/<section>_v1.json`
- Produces: A failing test that proves the expected Jiribilla files and schema/content contract are missing before implementation.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jiribilla_seed.py -q`

Expected: FAIL because `app/schemas/jiribilla` and `content/jiribilla` do not exist.

---

### Task 2: Add Jiribilla Schemas And Content

**Files:**
- Create: `app/schemas/jiribilla/hero/v1.json`
- Create: `app/schemas/jiribilla/mesa_uno/v1.json`
- Create: `app/schemas/jiribilla/proyectos/v1.json`
- Create: `app/schemas/jiribilla/eventos_privados/v1.json`
- Create: `app/schemas/jiribilla/glosario/v1.json`
- Create: `app/schemas/jiribilla/equipo/v1.json`
- Create: `app/schemas/jiribilla/footer/v1.json`
- Create: `app/schemas/jiribilla/social_links/v1.json`
- Create: `app/schemas/jiribilla/forms/v1.json`
- Create: `app/schemas/jiribilla/privacy_policy/v1.json`
- Create: matching `content/jiribilla/*_v1.json` files
- Test: `tests/test_jiribilla_seed.py`

**Interfaces:**
- Consumes: `tests/test_jiribilla_seed.py`
- Produces: Tenant schema/content fixtures discoverable by `scripts.seed_tenant_schemas` and `scripts.seed_tenant_content`.

- [ ] **Step 1: Create directories**

Run:

```powershell
New-Item -ItemType Directory -Force `
  app\schemas\jiribilla\hero, `
  app\schemas\jiribilla\mesa_uno, `
  app\schemas\jiribilla\proyectos, `
  app\schemas\jiribilla\eventos_privados, `
  app\schemas\jiribilla\glosario, `
  app\schemas\jiribilla\equipo, `
  app\schemas\jiribilla\footer, `
  app\schemas\jiribilla\social_links, `
  app\schemas\jiribilla\forms, `
  app\schemas\jiribilla\privacy_policy, `
  content\jiribilla | Out-Null
```

- [ ] **Step 2: Add schemas and content with `apply_patch`**

Add one `v1.json` schema per section and one matching content fixture per section. Use the PDF text exactly for initial values.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_jiribilla_seed.py -q`

Expected: PASS.

---

### Task 3: Add Jiribilla Bootstrap Script

**Files:**
- Create: `scripts/bootstrap_jiribilla.py`
- Test: `tests/test_jiribilla_bootstrap.py`

**Interfaces:**
- Consumes: `scripts.create_tenant.get_or_create_tenant`, `scripts.seed_tenant_schemas.run`, `scripts.seed_tenant_content.run`, and `scripts.bootstrap_tenant_settings.run`
- Produces: `run()` and `main()` for idempotent tenant bootstrap.

- [ ] **Step 1: Write failing tests**

```python
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


def test_jiribilla_bootstrap_content_paths_exist():
    for section in bootstrap_jiribilla.SECTIONS:
        assert Path(f"content/jiribilla/{section}_v1.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jiribilla_bootstrap.py -q`

Expected: FAIL with `ImportError` because `scripts.bootstrap_jiribilla` does not exist.

- [ ] **Step 3: Implement script**

Create `scripts/bootstrap_jiribilla.py` with constants, `_ensure_tenant()`, `_seed_content()`, `run()`, and `main()`. The script must create/reuse tenant `jiribilla`, load schemas, seed each entry as published, and publish settings with `hola@jiribilla.studio`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_jiribilla_seed.py tests/test_jiribilla_bootstrap.py -q`

Expected: PASS.

---

### Task 4: Add Admin Section Order For Jiribilla

**Files:**
- Modify: `app/web/admin/router.py`
- Test: `tests/test_jiribilla_admin_order.py`

**Interfaces:**
- Consumes: `_section_order_case_for_tenant_slug(tenant_slug: str | None)`
- Produces: Jiribilla sections ordered in the admin without affecting existing tenant order branches.

- [ ] **Step 1: Write failing test**

```python
from app.web.admin import router


def test_jiribilla_section_order_branch_exists():
    assert router._section_order_case_for_tenant_slug("jiribilla") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jiribilla_admin_order.py -q`

Expected: FAIL because `_section_order_case_for_tenant_slug("jiribilla")` returns `None`.

- [ ] **Step 3: Implement minimal admin order**

Add `_JIRIBILLA_SECTION_DASHBOARD_ORDER` with:

```python
[
    "hero",
    "mesa_uno",
    "proyectos",
    "eventos_privados",
    "glosario",
    "equipo",
    "footer",
    "social_links",
    "forms",
    "settings",
    "privacy_policy",
]
```

Add an `elif slug == "jiribilla"` branch in `_section_order_case_for_tenant_slug`.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_jiribilla_seed.py tests/test_jiribilla_bootstrap.py tests/test_jiribilla_admin_order.py -q`

Expected: PASS.

---

### Task 5: Final Verification

**Files:**
- No new files

**Interfaces:**
- Consumes: all previous task deliverables
- Produces: verified implementation result.

- [ ] **Step 1: Run Jiribilla test suite**

Run: `pytest tests/test_jiribilla_seed.py tests/test_jiribilla_bootstrap.py tests/test_jiribilla_admin_order.py -q`

Expected: PASS.

- [ ] **Step 2: Run related loader/admin tests**

Run: `pytest tests/test_registry_and_loader.py tests/test_ui_schema_endpoint.py -q`

Expected: PASS.

- [ ] **Step 3: Run git status**

Run: `git status --short`

Expected: only intentional Jiribilla files, the new docs plan, and the pre-existing untracked `memory/` and `schemas_pdfs/Jiribilla Schemas.pdf`.
