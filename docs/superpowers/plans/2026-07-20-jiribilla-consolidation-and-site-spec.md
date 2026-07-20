# Jiribilla Consolidation + Site Spec Endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Jiribilla a 4-entry dashboard that mirrors its single-page site, and give its front-end one public call that returns the whole published site keyed by block.

**Architecture:** Two phases with a hard gate between them. Phase 1 (Tasks 1–2, 8) is purely additive: a new public endpoint `GET /delivery/v1/sites/{tenant_slug}`, served only to allowlisted tenants, plus the Next.js integration guide. Phase 2 (Tasks 0, 3–7) collapses Jiribilla's nine editorial sections into two container sections.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, PostgreSQL (JSONB), Jinja2, pytest. No Alembic migration — no schema change.

## ⛔ Phase 2 gate (revision 2)

**Phase 2 must not run until the front-end has fully migrated to `GET /delivery/v1/sites/jiribilla`.**

Adversarial review disproved the original claim that consolidation would be invisible to the
front-end. Archiving the legacy entries breaks **both** public endpoints:

- List: `app/services/delivery_service.py:45-55` hard-filters `Entry.status == "published"`, so
  archived entries vanish from `/delivery/v1/entries?tenant_slug=jiribilla`.
- Detail: setting `status = "archived"` fires `Entry.updated_at`'s `onupdate=func.now()`
  (`app/models/content.py:70`), which pushes `updated_at` past every publish snapshot.
  `_latest_published_snapshot(..., not_older_than=updated_at)` then rejects the snapshot
  (`delivery_service.py:85-92`) and the fallback `if row["status"] != "published": return None`
  (`:361-362`) returns **404**.

The spec's line "they keep working indefinitely" is therefore **false** and is corrected in the
spec. Since the front-end already consumes the CMS partially, Phase 2 is sequenced behind an
explicit confirmation from the front-end developer. Keeping the legacy entries published instead
was rejected: it trades a loud 404 for silent content divergence, which is worse for a live site.

## Global Constraints

- **No behavioural or data change for any other tenant** (ANRO, OWA, DEWA, Ragni-Grady). Highest priority.
- The site payload endpoint serves **only allowlisted tenants** (`SITE_PAYLOAD_TENANTS`); every other slug gets 404. Adding a public aggregate over another tenant's content counts as a behavioural change.
- Do not modify `/delivery/v1/contact`, `/delivery/v1/entries`, or `/delivery/v1/tenants/{t}/sections/{s}/entries/{slug}`.
- Do not modify `app/services/ui_schema_service.py`.
- Jiribilla must keep a section whose key is literally `settings` (the shared contact endpoint resolves the recipient by that key for every tenant).
- Every migration statement filters by the resolved Jiribilla `tenant_id`; the script aborts if the slug is not `jiribilla`.
- Never `DELETE` content. Retire via `Entry.status = "archived"` plus a Jiribilla-guarded exclusion list.
- Run everything with the project venv: `.venv/Scripts/python.exe`.
- Public block keys are frozen: `hero`, `mesa_uno`, `proyectos`, `eventos_privados`, `glosario`, `equipo`, `forms`, `footer`, `social_links`, `privacy_policy`.

---

# PHASE 1 — additive, ships immediately

### Task 1: Site payload service + public endpoint

**Files:**
- Create: `app/services/site_payload_service.py`
- Create: `app/api/delivery/site.py`
- Modify: `app/main.py`
- Test: `tests/test_site_payload.py`

**Interfaces:**
- Produces: `build_site_payload(db: Session, tenant_slug: str) -> dict | None` → `{"tenant": {"slug", "name"}, "published_at": datetime | None, "blocks": dict[str, Any]}`; `None` when the tenant is missing, inactive, or not allowlisted.
- Produces: `PRIVATE_SECTION_KEYS: set[str]`, `SITE_PAYLOAD_TENANTS: set[str]`, `is_container_schema(schema) -> bool`.
- Consumes: `strip_internal_delivery_fields` (`app/services/delivery_service.py`); `_json_default`, `_to_utc_seconds` (`app/api/delivery/router.py`); `compute_etag_from_bytes`, `parse_httpdate`, `apply_delivery_cache_headers` (`app/services/publish_service.py`).

- [ ] **Step 1: Write the failing tests**

Covers: leaf sections, container spreading, draft/private/internal exclusion, the tenant
allowlist, 404s, and ETag revalidation.

```python
# tests/test_site_payload.py
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services import site_payload_service as sps

client = TestClient(app)


def _tenant(db: Session, slug="jiribilla", name="Jiribilla") -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is None:
        t = Tenant(slug=slug, name=name, is_active=True)
        db.add(t)
        db.flush()
    t.is_active = True
    db.flush()
    return t


def _section_with_entry(db, tenant, key, data, *, schema=None, status="published"):
    section = db.scalar(
        select(Section).where(Section.tenant_id == tenant.id, Section.key == key)
    )
    if section is None:
        section = Section(tenant_id=tenant.id, key=key, name=key.title())
        db.add(section)
        db.flush()
        db.add(SectionSchema(
            tenant_id=tenant.id, section_id=section.id, version=1,
            title=f"{key} v1", schema=schema or {"type": "object"}, is_active=True,
        ))
    entry = Entry(
        tenant_id=tenant.id, section_id=section.id, slug=key,
        schema_version=1, status=status, data=data,
    )
    db.add(entry)
    db.flush()
    return section, entry


def test_leaf_sections_become_blocks_keyed_by_section_key(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_hero_leaf", {"heroText": "Hola"})

    body = client.get(f"/delivery/v1/sites/{t.slug}").json()
    assert body["tenant"] == {"slug": t.slug, "name": t.name}
    assert body["blocks"]["zz_hero_leaf"] == {"heroText": "Hola"}


def test_container_section_spreads_its_blocks(db: Session):
    t = _tenant(db)
    _section_with_entry(
        db, t, "zz_container",
        {"zz_a": {"text": "A"}, "zz_b": {"text": "B"}},
        schema={"type": "object", "x-ui": {"container": True}},
    )

    body = client.get(f"/delivery/v1/sites/{t.slug}").json()
    assert body["blocks"]["zz_a"] == {"text": "A"}
    assert body["blocks"]["zz_b"] == {"text": "B"}
    assert "zz_container" not in body["blocks"]


def test_drafts_private_sections_and_internal_keys_are_excluded(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_pub", {"v": "Publicado", "__draft": {"v": "Borrador"}})
    _section_with_entry(db, t, "zz_draft", {"v": "x"}, status="draft")
    _section_with_entry(db, t, "settings", {"contact_email": "a@b.com"})
    _section_with_entry(db, t, "mensajes_eventos", {"title": "Mensajes"})

    blocks = client.get(f"/delivery/v1/sites/{t.slug}").json()["blocks"]
    assert blocks["zz_pub"] == {"v": "Publicado"}
    assert "zz_draft" not in blocks
    assert "settings" not in blocks
    assert "mensajes_eventos" not in blocks


def test_non_allowlisted_tenant_is_404_even_if_it_exists(db: Session):
    """Other tenants' content must never be exposed through this aggregate."""
    other = _tenant(db, slug="owa", name="OWA")
    _section_with_entry(db, other, "zz_owa_block", {"v": "secreto"})
    assert "owa" not in sps.SITE_PAYLOAD_TENANTS
    assert client.get("/delivery/v1/sites/owa").status_code == 404


def test_unknown_tenant_is_404(db: Session):
    assert client.get("/delivery/v1/sites/no-existe-jamas").status_code == 404


def test_inactive_tenant_is_404(db: Session):
    t = _tenant(db)
    t.is_active = False
    db.flush()
    assert client.get(f"/delivery/v1/sites/{t.slug}").status_code == 404


def test_etag_returns_304(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_etag", {"v": "1"})

    first = client.get(f"/delivery/v1/sites/{t.slug}")
    etag = first.headers.get("ETag")
    assert etag
    second = client.get(f"/delivery/v1/sites/{t.slug}", headers={"If-None-Match": etag})
    assert second.status_code == 304
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_site_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.site_payload_service`.

- [ ] **Step 3: Implement the service**

```python
# app/services/site_payload_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services.delivery_service import strip_internal_delivery_fields

# Tenants allowed to expose a whole-site aggregate. Opt-in on purpose: this
# endpoint returns a tenant's entire published content tree in one public call,
# so no project gets that surface without being listed here.
SITE_PAYLOAD_TENANTS = {"jiribilla"}

# Section keys that must never reach the public site payload.
PRIVATE_SECTION_KEYS = {
    "settings",
    "mensajes",
    "mensajes_eventos",
    "mensajes_bolsa",
}


def _active_schema_dict(db: Session, tenant_id: int, section_id: int) -> dict[str, Any]:
    row = db.scalar(
        select(SectionSchema)
        .where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.is_active.is_(True),
            )
        )
        .order_by(SectionSchema.version.desc())
        .limit(1)
    )
    schema = getattr(row, "schema", None)
    return schema if isinstance(schema, dict) else {}


def is_container_schema(schema: Any) -> bool:
    """A container section spreads its top-level keys as site blocks."""
    if not isinstance(schema, dict):
        return False
    x_ui = schema.get("x-ui")
    return isinstance(x_ui, dict) and x_ui.get("container") is True


def _newer(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    a_aware = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    b_aware = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return a if a_aware >= b_aware else b


def build_site_payload(db: Session, tenant_slug: str) -> dict[str, Any] | None:
    """
    Whole published site for a tenant, keyed by block.

    Container sections spread their top-level properties as blocks; every other
    section contributes one block under its own section key. Container blocks win
    over a same-named leaf section, so the payload stays stable while a tenant is
    mid-consolidation.

    Returns None when the tenant is not allowlisted, does not exist, or is inactive.
    """
    if tenant_slug not in SITE_PAYLOAD_TENANTS:
        return None

    tenant = db.scalar(
        select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_active.is_(True))
    )
    if not tenant:
        return None

    rows = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(and_(Entry.tenant_id == tenant.id, Entry.status == "published"))
        .order_by(Entry.id.asc())
    ).all()

    blocks: dict[str, Any] = {}
    container_keys: set[str] = set()
    published_at: datetime | None = None

    for entry, section in rows:
        if section.key in PRIVATE_SECTION_KEYS:
            continue

        data = strip_internal_delivery_fields(entry.data or {})
        if not isinstance(data, dict):
            continue

        if is_container_schema(_active_schema_dict(db, int(tenant.id), int(section.id))):
            for block_key, block_value in data.items():
                blocks[block_key] = block_value
                container_keys.add(block_key)
        elif section.key not in container_keys:
            blocks[section.key] = data

        published_at = _newer(published_at, entry.published_at)
        published_at = _newer(published_at, entry.updated_at)

    return {
        "tenant": {"slug": tenant.slug, "name": tenant.name},
        "published_at": published_at,
        "blocks": blocks,
    }
```

- [ ] **Step 4: Implement the endpoint**

```python
# app/api/delivery/site.py
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.delivery.router import _json_default, _to_utc_seconds
from app.db.session import get_db
from app.services.publish_service import (
    apply_delivery_cache_headers,
    compute_etag_from_bytes,
    parse_httpdate,
)
from app.services.site_payload_service import build_site_payload

router = APIRouter(prefix="/delivery/v1", tags=["Delivery"])


@router.get("/sites/{tenant_slug}", summary="Sitio completo publicado (público)")
def get_site_payload(
    tenant_slug: str,
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Devuelve todo el contenido publicado de un sitio en una sola llamada,
    agrupado por bloque. Solo disponible para tenants habilitados; las
    secciones internas (settings, bandejas de mensajes) nunca se exponen.
    """
    payload = build_site_payload(db, tenant_slug)
    if payload is None:
        raise HTTPException(status_code=404, detail="Site not found")

    body_bytes = json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)
    last_modified = _to_utc_seconds(payload.get("published_at"))

    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
        return resp

    if if_modified_since and last_modified:
        ims = _to_utc_seconds(parse_httpdate(if_modified_since))
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
            return resp

    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
    return resp
```

- [ ] **Step 5: Register the router**

In `app/main.py`, add next to the other delivery imports:

```python
from app.api.delivery.site import router as site_router
```

and next to `app.include_router(delivery_router)`:

```python
app.include_router(site_router)
```

Both must sit above the `_mark_delivery_routes_public(app)` call at the bottom of the file, which
is already true of that block. A second router with the same `/delivery/v1` prefix is valid —
`app/api/delivery/contact.py:20` already does it.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_site_payload.py -q`
Expected: PASS — 7 passed.

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 57 passed + 7 new, 1 skipped. No regressions.

- [ ] **Step 8: Commit**

```bash
git add app/services/site_payload_service.py app/api/delivery/site.py app/main.py tests/test_site_payload.py
git commit -m "feat: add opt-in whole-site payload endpoint"
```

---

### Task 2: Cross-tenant isolation guard (a test that can actually fail)

The original version of this task computed values and asserted only that they were non-empty —
it could not fail. This version pins a real golden file.

**Files:**
- Create: `tests/fixtures/tenant_isolation_baseline.json`
- Test: `tests/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `is_container_schema`, `SITE_PAYLOAD_TENANTS` from Task 1.

- [ ] **Step 1: Write the test**

```python
# tests/test_tenant_isolation.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Section, SectionSchema
from app.services.site_payload_service import SITE_PAYLOAD_TENANTS, is_container_schema

OTHER_TENANTS = ["anro", "owa", "dewa", "ragni-grady"]
BASELINE = Path(__file__).parent / "fixtures" / "tenant_isolation_baseline.json"


def _section_keys(db: Session, slug: str) -> list[str]:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        pytest.skip(f"tenant {slug} not seeded in this database")
    return sorted(
        key for (key,) in db.execute(
            select(Section.key).where(Section.tenant_id == tenant.id)
        ).all()
    )


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_section_keys_match_baseline(db: Session, slug: str):
    """Fails loudly if Jiribilla work adds, renames or removes another tenant's sections."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if slug not in baseline:
        pytest.skip(f"{slug} not captured in the baseline")
    assert _section_keys(db, slug) == baseline[slug]


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_have_no_container_sections(db: Session, slug: str):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        pytest.skip(f"tenant {slug} not seeded in this database")
    schemas = db.scalars(
        select(SectionSchema).where(SectionSchema.tenant_id == tenant.id)
    ).all()
    offenders = sorted({s.section_id for s in schemas if is_container_schema(s.schema)})
    assert offenders == [], f"{slug} gained container sections: {offenders}"


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_are_not_exposed_by_the_site_endpoint(slug: str):
    assert slug not in SITE_PAYLOAD_TENANTS
```

- [ ] **Step 2: Generate the baseline fixture**

```bash
.venv/Scripts/python.exe -c "
import json, pathlib
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Section
db = SessionLocal()
out = {}
for slug in ['anro','owa','dewa','ragni-grady']:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is None:
        continue
    out[slug] = sorted(k for (k,) in db.execute(select(Section.key).where(Section.tenant_id == t.id)).all())
p = pathlib.Path('tests/fixtures'); p.mkdir(parents=True, exist_ok=True)
(p / 'tenant_isolation_baseline.json').write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(out, indent=2, ensure_ascii=False))
"
```

- [ ] **Step 3: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tenant_isolation.py -q`
Expected: PASS, with skips only for tenants absent from the local DB.

- [ ] **Step 4: Commit**

```bash
git add tests/test_tenant_isolation.py tests/fixtures/tenant_isolation_baseline.json
git commit -m "test: pin cross-tenant isolation with a real baseline"
```

---

### Task 8 (runs in Phase 1): Next.js integration guide

Ships with Phase 1 so the front-end can migrate — which is what unlocks the Phase 2 gate.

**Files:**
- Create: `docs/jiribilla-frontend-integration.md`
- Modify: `docs/jiribilla-forms-frontend.md` (cross-link)

- [ ] **Step 1: Verify the live payload first**

```bash
curl -s https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla | .venv/Scripts/python.exe -m json.tool | head -80
```

Every field documented in the guide must appear in that output. Document nothing you have not seen.

- [ ] **Step 2: Write the guide**

In Spanish, App Router (Next.js 14/15), containing:
- Base URL and the single call `GET /delivery/v1/sites/jiribilla`.
- The complete `blocks` contract: every block key, its fields, which are arrays
  (`proyectos.projects`, `glosario.definitions`, `equipo.gallery`, `proyectos.projects[].projectAwards`),
  and the `{url, alt}` image shape.
- TypeScript types for the payload.
- A `getSite()` fetcher using `fetch(url, { next: { revalidate: 60 } })`, plus the
  `cache: "no-store"` variant, noting the endpoint sends `ETag`/`Last-Modified` and answers `304`.
- A worked example rendering one block server-side.
- The stability note: block keys are frozen; the per-section endpoints still work today, so
  migration can be gradual — **but** they will be retired once migration completes, so the site
  should end up calling only `/sites/jiribilla`.
- Defensive-rendering caveat: arrays start empty and image URLs blank until the client fills the
  dashboard.
- A short pointer to the two form endpoints in `docs/jiribilla-forms-frontend.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/jiribilla-frontend-integration.md docs/jiribilla-forms-frontend.md
git commit -m "docs: add Next.js integration guide for the Jiribilla site payload"
```

---

# PHASE 2 — blocked by the gate above

Do not start these until the front-end developer confirms the site reads only
`/delivery/v1/sites/jiribilla`.

### Task 0: Spike — nested repeater inside a container section

Decision gate for the grouping. `proyectos` is an array of projects, each holding an array of
award images. The editor derives tabs from **data keys**, not schema properties
(`app/services/ui_schema_service.py:635` — `known = [k for k in order if k in data]`), and this
combination is unproven. Throwaway work; nothing is committed.

- [ ] **Step 1:** Create a temp section `spike_container` on the Jiribilla tenant, schema root
  `{"type":"object","x-ui":{"container":true,"order":["proyectos"]},"properties":{"proyectos": …},"$defs":{…}}`
  with **all** `$defs` hoisted to the root, and one draft entry holding two projects, each with two award images.
- [ ] **Step 2:** Open `/admin/pages/{entry_id}/edit`. Confirm the Proyectos tab appears, the
  projects repeater renders, "Add Project" works, the nested awards repeater renders, and saving
  round-trips the nested arrays.
- [ ] **Step 3:** Record the verdict; delete the spike section either way.
  **PASS** → 4 entries. **FAIL** → `proyectos` stays its own page (5 entries); update Task 3's
  `PAGINA_BLOCKS` and the spec's table. Do **not** patch `ui_schema_service.py` — it is shared code.

---

### Task 3: Container schemas for `pagina_principal` and `global`

**Files:**
- Create: `app/schemas/jiribilla/pagina_principal/v1.json`, `app/schemas/jiribilla/global/v1.json`
- Modify: `tests/test_jiribilla_seed.py` (its `EXPECTED_SECTIONS` list pins the schema dirs)
- Test: `tests/test_jiribilla_container_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jiribilla_container_schemas.py
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "app" / "schemas" / "jiribilla"

PAGINA_BLOCKS = ["hero", "mesa_uno", "proyectos", "eventos_privados", "glosario", "equipo", "forms"]
GLOBAL_BLOCKS = ["footer", "social_links", "privacy_policy"]


def _load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name / "v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,blocks", [("pagina_principal", PAGINA_BLOCKS), ("global", GLOBAL_BLOCKS)])
def test_container_schema_shape(name: str, blocks: list[str]):
    schema = _load(name)
    assert schema["type"] == "object"
    assert schema["x-ui"]["container"] is True
    assert schema["x-ui"]["order"] == blocks
    assert list(schema["properties"].keys()) == blocks
    for block in blocks:
        assert "x-ui" in schema["properties"][block], f"{block} needs an x-ui label for its tab"


@pytest.mark.parametrize("name,blocks", [("pagina_principal", PAGINA_BLOCKS), ("global", GLOBAL_BLOCKS)])
def test_blocks_preserve_original_property_names(name: str, blocks: list[str]):
    """Payload stability: each block keeps the exact properties of its old section schema."""
    container = _load(name)
    for block in blocks:
        original = _load(block)
        assert set(container["properties"][block]["properties"].keys()) == set(original["properties"].keys()), block


@pytest.mark.parametrize("name", ["pagina_principal", "global"])
def test_every_local_ref_resolves_from_the_container_root(name: str):
    """
    JSON Pointer '#/$defs/X' always resolves from the DOCUMENT root, so every $defs
    used by a copied block (Image, Project, Definition, ...) must be hoisted.
    """
    raw = (SCHEMA_DIR / name / "v1.json").read_text(encoding="utf-8")
    schema = json.loads(raw)
    defs = set((schema.get("$defs") or {}).keys())
    referenced = set(re.findall(r'"#/\$defs/([A-Za-z0-9_]+)"', raw))
    missing = referenced - defs
    assert missing == set(), f"{name} references unhoisted $defs: {sorted(missing)}"
    for block in schema["properties"].values():
        assert "$defs" not in block, "block-level $defs never resolve; hoist them to the root"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_container_schemas.py -q`
Expected: FAIL — `FileNotFoundError` for `pagina_principal/v1.json`.

- [ ] **Step 3: Author the container schemas**

Copy each existing section schema body under its block name. Rules:

- Property names identical — payload stability depends on it.
- **Hoist every `$defs` to the container root**: `Image` (in `proyectos`, `equipo`,
  `eventos_privados`), `Project` (`app/schemas/jiribilla/proyectos/v1.json:44`) and
  `Definition` (`app/schemas/jiribilla/glosario/v1.json:23`). Before merging the three `Image`
  definitions, diff them and confirm they are identical.
- Every block gets an `x-ui.label` (it becomes the tab name); inner `x-ui` hints
  (`textarea`, `widget: image`, `itemTitlePath`, `addLabel`) are preserved verbatim.
- If Task 0 returned FAIL, drop `proyectos` from `pagina_principal` and from `PAGINA_BLOCKS`.

- [ ] **Step 4: Update the seed test that pins schema directories**

`tests/test_jiribilla_seed.py:35-38` asserts the schema dirs equal `EXPECTED_SECTIONS` exactly.
Add `"pagina_principal"` and `"global"` to that list, keeping the legacy entries (the files stay
on disk as the source of truth for the copy and for rollback).

- [ ] **Step 5: Run both test files**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_container_schemas.py tests/test_jiribilla_seed.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/jiribilla/pagina_principal app/schemas/jiribilla/global tests/test_jiribilla_container_schemas.py tests/test_jiribilla_seed.py
git commit -m "feat: add Jiribilla container schemas"
```

---

### Task 4: Migration script + payload-equality proof

**Files:**
- Create: `scripts/migrate_jiribilla_sections.py`
- Test: `tests/test_jiribilla_migration.py`

**Interfaces:**
- Produces: `run(*, db: Session | None = None, tenant_slug: str = "jiribilla", dry_run: bool = False) -> dict` returning `{"created": [...], "moved": [...], "archived": [...], "skipped": [...]}`.
- Produces: `TENANT_SLUG: str`, `CONTAINERS: dict[str, list[str]]`.

Fixes folded in from review: published-only sources (no draft leak), missing sources seeded as
`{}` so the tab still renders, nested `__draft` stripped, `published_at` set on the container, and
`rollback()` guarded so it never touches a caller-supplied session.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jiribilla_migration.py
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Entry, Section
from app.services.site_payload_service import build_site_payload
from scripts import migrate_jiribilla_sections as mig


def _jiribilla(db: Session) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == "jiribilla"))
    if t is None:
        pytest.skip("jiribilla tenant not seeded in this database")
    return t


def _container(db: Session, tenant: Tenant, key: str) -> Entry:
    return db.scalar(
        select(Entry).join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tenant.id, Section.key == key)
    )


def test_site_payload_blocks_are_identical_before_and_after(db: Session):
    """The contract the front-end depends on."""
    _jiribilla(db)
    before = build_site_payload(db, "jiribilla")["blocks"]
    mig.run(db=db)
    after = build_site_payload(db, "jiribilla")["blocks"]
    assert after == before


def test_migration_is_idempotent(db: Session):
    _jiribilla(db)
    mig.run(db=db)
    first = build_site_payload(db, "jiribilla")["blocks"]
    mig.run(db=db)
    assert build_site_payload(db, "jiribilla")["blocks"] == first


def test_second_run_does_not_overwrite_edited_block(db: Session):
    tenant = _jiribilla(db)
    mig.run(db=db)
    container = _container(db, tenant, "pagina_principal")
    data = dict(container.data)
    data["hero"] = {"heroText": "EDITADO DESPUES DE MIGRAR"}
    container.data = data
    db.flush()

    mig.run(db=db)
    assert _container(db, tenant, "pagina_principal").data["hero"] == {"heroText": "EDITADO DESPUES DE MIGRAR"}


def test_draft_sources_are_not_copied_into_a_published_container(db: Session):
    """Copying a draft into a published container would leak unpublished content."""
    tenant = _jiribilla(db)
    hero_section = db.scalar(
        select(Section).where(Section.tenant_id == tenant.id, Section.key == "hero")
    )
    hero_entry = db.scalar(select(Entry).where(Entry.section_id == hero_section.id))
    hero_entry.status = "draft"
    hero_entry.data = {"heroText": "SECRETO EN BORRADOR"}
    db.flush()

    mig.run(db=db)
    assert _container(db, tenant, "pagina_principal").data["hero"] == {}


def test_missing_source_still_gets_an_editable_block(db: Session):
    """Editor tabs come from data keys, so an absent block must be seeded empty."""
    tenant = _jiribilla(db)
    report = mig.run(db=db)
    data = _container(db, tenant, "pagina_principal").data
    for block in mig.CONTAINERS["pagina_principal"]:
        assert block in data, f"{block} would have no tab"
    assert isinstance(report["skipped"], list)


def test_nested_draft_keys_are_stripped(db: Session):
    tenant = _jiribilla(db)
    hero_section = db.scalar(
        select(Section).where(Section.tenant_id == tenant.id, Section.key == "hero")
    )
    hero_entry = db.scalar(select(Entry).where(Entry.section_id == hero_section.id))
    hero_entry.status = "published"
    hero_entry.data = {"heroText": "Publicado", "__draft": {"heroText": "Borrador"}}
    db.flush()

    mig.run(db=db)
    assert _container(db, tenant, "pagina_principal").data["hero"] == {"heroText": "Publicado"}


def test_container_gets_published_at(db: Session):
    tenant = _jiribilla(db)
    mig.run(db=db)
    assert _container(db, tenant, "pagina_principal").published_at is not None


def test_legacy_entries_are_archived_not_deleted(db: Session):
    tenant = _jiribilla(db)
    mig.run(db=db)
    for key in ("hero", "mesa_uno", "footer"):
        section = db.scalar(select(Section).where(Section.tenant_id == tenant.id, Section.key == key))
        assert section is not None, f"{key} section was deleted"
        entry = db.scalar(select(Entry).where(Entry.section_id == section.id))
        assert entry is not None and entry.status == "archived"


def test_migration_refuses_a_non_jiribilla_tenant(db: Session):
    with pytest.raises(RuntimeError, match="jiribilla"):
        mig.run(db=db, tenant_slug="owa")


def test_other_tenants_are_untouched(db: Session):
    other = db.scalar(select(Tenant).where(Tenant.slug == "owa"))
    if other is None:
        pytest.skip("owa tenant not seeded")

    def snapshot():
        return {
            (s.key, e.slug): e.status
            for s, e in db.execute(
                select(Section, Entry).join(Entry, Entry.section_id == Section.id)
                .where(Entry.tenant_id == other.id)
            ).all()
        }

    before = snapshot()
    mig.run(db=db)
    assert snapshot() == before
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_migration.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.migrate_jiribilla_sections`.

- [ ] **Step 3: Implement the migration**

```python
# scripts/migrate_jiribilla_sections.py
from __future__ import annotations

"""
Consolidate Jiribilla's editorial sections into container sections.

Idempotent and re-runnable. Hard-scoped to the Jiribilla tenant: aborts before
writing anything if the requested slug is not 'jiribilla'. Legacy sections are
archived, never deleted.

PRECONDITION: the front-end must already read /delivery/v1/sites/jiribilla.
Archiving the legacy entries makes the per-section delivery endpoints 404.

Usage:
    python -m scripts.migrate_jiribilla_sections [--dry-run]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.auth import Tenant  # noqa: E402
from app.models.content import Entry, Section, SectionSchema  # noqa: E402
from app.services.delivery_service import strip_internal_delivery_fields  # noqa: E402

TENANT_SLUG = "jiribilla"

CONTAINERS: dict[str, list[str]] = {
    "pagina_principal": ["hero", "mesa_uno", "proyectos", "eventos_privados", "glosario", "equipo", "forms"],
    "global": ["footer", "social_links", "privacy_policy"],
}

CONTAINER_LABELS = {"pagina_principal": "Página principal", "global": "Global"}

SCHEMA_DIR = ROOT / "app" / "schemas" / TENANT_SLUG


def _load_container_schema(key: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / key / "v1.json").read_text(encoding="utf-8"))


def _ensure_container(db: Session, tenant: Tenant, key: str, report: dict) -> Entry:
    section = db.scalar(
        select(Section).where(and_(Section.tenant_id == tenant.id, Section.key == key))
    )
    if section is None:
        section = Section(
            tenant_id=tenant.id, key=key, name=CONTAINER_LABELS[key],
            description="Container section — edited as tabs.",
        )
        db.add(section)
        db.flush()
        report["created"].append(f"section:{key}")

    schema_rec = db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == tenant.id,
                SectionSchema.section_id == section.id,
                SectionSchema.version == 1,
            )
        )
    )
    if schema_rec is None:
        db.add(SectionSchema(
            tenant_id=tenant.id, section_id=section.id, version=1,
            title=f"{CONTAINER_LABELS[key]} v1", schema=_load_container_schema(key),
            is_active=True,
        ))
        db.flush()
        report["created"].append(f"schema:{key}")

    entry = db.scalar(
        select(Entry).where(
            and_(
                Entry.tenant_id == tenant.id,
                Entry.section_id == section.id,
                Entry.slug == key,
            )
        )
    )
    if entry is None:
        entry = Entry(
            tenant_id=tenant.id, section_id=section.id, slug=key,
            schema_version=1, status="published", data={},
            published_at=datetime.now(timezone.utc),
        )
        db.add(entry)
        db.flush()
        report["created"].append(f"entry:{key}")
    return entry


def _published_source(db: Session, tenant: Tenant, block_key: str) -> Entry | None:
    """Only published sources are copied — a draft must never reach a published container."""
    return db.scalar(
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .where(
            and_(
                Entry.tenant_id == tenant.id,
                Section.key == block_key,
                Entry.status == "published",
            )
        )
        .order_by(Entry.id.asc())
        .limit(1)
    )


def run(*, db: Session | None = None, tenant_slug: str = TENANT_SLUG, dry_run: bool = False) -> dict:
    if tenant_slug != TENANT_SLUG:
        raise RuntimeError(
            f"This migration only runs for the '{TENANT_SLUG}' tenant, refusing '{tenant_slug}'."
        )

    owns_session = db is None
    db = db or SessionLocal()
    report: dict[str, list[str]] = {"created": [], "moved": [], "archived": [], "skipped": []}

    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            raise RuntimeError(f"Tenant not found: {TENANT_SLUG}")

        for container_key, block_keys in CONTAINERS.items():
            container_entry = _ensure_container(db, tenant, container_key, report)
            data = dict(container_entry.data or {})

            for block_key in block_keys:
                if block_key in data:
                    continue  # already migrated — never overwrite edited content
                source = _published_source(db, tenant, block_key)
                if source is None:
                    # Editor tabs derive from data keys, so seed an empty block or the
                    # section becomes uneditable forever.
                    data[block_key] = {}
                    report["skipped"].append(block_key)
                    continue
                data[block_key] = strip_internal_delivery_fields(source.data or {})
                report["moved"].append(f"{block_key} -> {container_key}")

            container_entry.data = data
            container_entry.status = "published"
            if container_entry.published_at is None:
                container_entry.published_at = datetime.now(timezone.utc)

        # Retire the sources only after every block has been copied.
        for block_keys in CONTAINERS.values():
            for block_key in block_keys:
                source = _published_source(db, tenant, block_key)
                if source is not None:
                    source.status = "archived"
                    report["archived"].append(block_key)

        if dry_run:
            if owns_session:
                db.rollback()
        elif owns_session:
            db.commit()
        else:
            db.flush()
        return report
    except Exception:
        if owns_session:
            db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate Jiribilla sections.")
    parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

Note: `--dry-run` only rolls back when the script owns the session; when a session is injected
(tests) the caller controls the transaction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_migration.py -q`
Expected: PASS — 10 passed.

- [ ] **Step 5: Full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_jiribilla_sections.py tests/test_jiribilla_migration.py
git commit -m "feat: add idempotent Jiribilla section consolidation migration"
```

---

### Task 5: Merge the two inboxes into one tabbed `mensajes` page

**Files:**
- Modify: `app/web/admin/router.py` — `_JIRIBILLA_INBOX_SECTIONS` (**stays a dict**),
  `_jiribilla_inbox_template_response` (:329-…), `page_edit_get` (:2124-2125),
  `_load_jiribilla_message_or_404` (:2690-2715), and both POST routes (:2718, :2734)
- Modify: `app/templates/admin/jiribilla_inbox.html` (tab strip)
- Modify: `scripts/bootstrap_jiribilla.py` (`INBOX_SECTIONS`)
- Test: `tests/test_jiribilla_forms.py`

**Interfaces:**
- Produces: `_JIRIBILLA_INBOX_TABS: dict[str, str]` = `{"eventos": FORM_TYPE_EVENTOS, "bolsa": FORM_TYPE_BOLSA}`.
- Produces: `_jiribilla_form_type_for(section_key: str, tab: str | None) -> str`.
- `_jiribilla_inbox_template_response(..., form_type: str)` takes the form type as a parameter.
- `_load_jiribilla_message_or_404(db, request, entry_id, submission_id, tab)` accepts the tab.

Review fixes folded in: `_JIRIBILLA_INBOX_SECTIONS` must remain subscriptable because
`router.py:423` and `router.py:2711` index it; and both POST routes must receive `?form=` or the
Bolsa tab's toggle-read/delete silently no-op.

- [ ] **Step 1: Update the tests that pin this behaviour**

In `tests/test_jiribilla_forms.py`, replace `test_jiribilla_inbox_section_map` and fix
`test_jiribilla_dashboard_order_includes_inboxes` (which asserts the legacy keys and will break in
Task 6):

```python
def test_jiribilla_inbox_tabs_map():
    assert admin_router._JIRIBILLA_INBOX_TABS == {
        "eventos": FORM_TYPE_EVENTOS,
        "bolsa": FORM_TYPE_BOLSA,
    }


def test_jiribilla_form_type_resolution():
    resolve = admin_router._jiribilla_form_type_for
    assert resolve("mensajes", "bolsa") == FORM_TYPE_BOLSA
    assert resolve("mensajes", None) == FORM_TYPE_EVENTOS
    assert resolve("mensajes_bolsa", None) == FORM_TYPE_BOLSA  # legacy section still resolves


def test_jiribilla_dashboard_order_includes_mensajes():
    assert "mensajes" in admin_router._JIRIBILLA_SECTION_DASHBOARD_ORDER
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_forms.py -q`
Expected: FAIL — `AttributeError: _JIRIBILLA_INBOX_TABS`.

- [ ] **Step 3: Implement**

```python
# Keep this a dict: router.py:423 and router.py:2711 subscript it.
_JIRIBILLA_INBOX_SECTIONS = {
    "mensajes": None,               # tabbed view; form type comes from ?form=
    "mensajes_eventos": FORM_TYPE_EVENTOS,   # legacy, until Task 6 hides them
    "mensajes_bolsa": FORM_TYPE_BOLSA,
}

_JIRIBILLA_INBOX_TABS = {"eventos": FORM_TYPE_EVENTOS, "bolsa": FORM_TYPE_BOLSA}


def _jiribilla_form_type_for(section_key: str, tab: str | None) -> str:
    legacy = _JIRIBILLA_INBOX_SECTIONS.get(section_key)
    if legacy:
        return legacy
    return _JIRIBILLA_INBOX_TABS.get((tab or "eventos").strip().lower(), FORM_TYPE_EVENTOS)
```

- `page_edit_get` gains `form: Optional[str] = Query(default=None)` and passes
  `form_type=_jiribilla_form_type_for(section.key, form)`.
- `_jiribilla_inbox_template_response` takes `form_type` instead of deriving it, and receives
  `active_tab` for template highlighting.
- `_load_jiribilla_message_or_404` gains a `tab` argument and uses
  `_jiribilla_form_type_for(section.key, tab)`; **both** POST routes gain
  `form: Optional[str] = Query(default=None)` and pass it through, then redirect back to
  `/admin/pages/{entry_id}/edit?form={tab}` so the user stays on the tab they acted from.
- The template renders a tab strip (links to `?form=eventos` / `?form=bolsa`, active one marked)
  only when `page.section_key == "mensajes"`.
- In `scripts/bootstrap_jiribilla.py`, set `INBOX_SECTIONS = {"mensajes": "Mensajes"}` and leave
  the legacy keys untouched so existing installs keep working until Task 6.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_forms.py -q`
Expected: PASS.

- [ ] **Step 5: Manually verify both tabs**

Open the merged page, switch to Bolsa, delete and toggle-read a message, and confirm the action
applies to the **bolsa** submission and returns to the Bolsa tab.

- [ ] **Step 6: Commit**

```bash
git add app/web/admin/router.py app/templates/admin/jiribilla_inbox.html scripts/bootstrap_jiribilla.py tests/test_jiribilla_forms.py
git commit -m "feat: merge Jiribilla inboxes into a single tabbed page"
```

---

### Task 6: Dashboard order, legacy hiding, `settings` relabel

**Files:**
- Modify: `app/web/admin/router.py` (`_JIRIBILLA_SECTION_DASHBOARD_ORDER`, `pages_list` at :1957 and :2000)
- Modify: `scripts/bootstrap_jiribilla.py` (`SECTION_LABELS`)
- Test: `tests/test_jiribilla_admin_order.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jiribilla_admin_order.py  (replace the file)
from app.web.admin import router


def test_jiribilla_section_order_branch_exists():
    assert router._section_order_case_for_tenant_slug("jiribilla") is not None


def test_jiribilla_dashboard_shows_four_entries():
    assert router._JIRIBILLA_SECTION_DASHBOARD_ORDER == [
        "pagina_principal", "global", "mensajes", "settings",
    ]


def test_legacy_keys_are_hidden_but_settings_is_not():
    legacy = router._JIRIBILLA_LEGACY_SECTION_KEYS
    for key in ("hero", "mesa_uno", "proyectos", "eventos_privados", "glosario", "equipo",
                "forms", "footer", "social_links", "privacy_policy",
                "mensajes_eventos", "mensajes_bolsa"):
        assert key in legacy
    assert "settings" not in legacy, "settings must stay visible and keyed 'settings'"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jiribilla_admin_order.py -q`
Expected: FAIL — the order list still holds 13 keys.

- [ ] **Step 3: Implement**

```python
_JIRIBILLA_SECTION_DASHBOARD_ORDER = ["pagina_principal", "global", "mensajes", "settings"]

# Consolidated away. Kept in the DB (archived) so the change is reversible.
_JIRIBILLA_LEGACY_SECTION_KEYS = {
    "hero", "mesa_uno", "proyectos", "eventos_privados", "glosario", "equipo",
    "forms", "footer", "social_links", "privacy_policy",
    "mensajes_eventos", "mensajes_bolsa",
}


def _is_jiribilla_active(active: dict | None) -> bool:
    return ((active or {}).get("slug") or "").strip().lower() == "jiribilla"
```

In `pages_list`, right after the OWA guard at `app/web/admin/router.py:1957-1958`:

```python
    if _is_jiribilla_active(active):
        base = base.where(Section.key.notin_(_JIRIBILLA_LEGACY_SECTION_KEYS))
```

and mirrored on `sects_query` after `:2000`:

```python
    if _is_jiribilla_active(active):
        sects_query = sects_query.where(Section.key.notin_(_JIRIBILLA_LEGACY_SECTION_KEYS))
```

In `scripts/bootstrap_jiribilla.py`, set `SECTION_LABELS["settings"] = "Configuración"` and add
labels for both containers.

Also note for the reviewer: `privacy_policy`'s special-cased editor branches
(`router.py:2140-2143`, `:2177-2184`, `:2594-2600`, `_normalize_privacy_payload`) stop firing once
it is a block inside `global`; it falls back to the generic renderer. Verify the field still edits
correctly during Step 5.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, including `tests/test_tenant_isolation.py`.

- [ ] **Step 5: Visual check**

Four entries in the pages list; Página principal tabs render (including the Proyectos repeater);
Mensajes switches tabs; Configuración edits the destination emails; the privacy policy body still
saves.

- [ ] **Step 6: Commit**

```bash
git add app/web/admin/router.py scripts/bootstrap_jiribilla.py tests/test_jiribilla_admin_order.py
git commit -m "feat: collapse Jiribilla dashboard to four entries"
```

---

### Task 7: Rollout

- [ ] **Step 1: Confirm the gate**

Written confirmation from the front-end developer that the site reads only
`/delivery/v1/sites/jiribilla`. Without it, stop here.

- [ ] **Step 2: Record the legacy URLs that will break**

```bash
for s in hero mesa_uno proyectos eventos_privados glosario equipo forms footer social_links privacy_policy; do
  echo -n "$s: "
  curl -s -o /dev/null -w "%{http_code}\n" "https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/tenants/jiribilla/sections/$s/entries/$s"
done
```

These all return 200 today and **will return 404 after the migration**. That is expected and is
precisely what the gate protects.

- [ ] **Step 3: Local dry run**

Run: `.venv/Scripts/python.exe -m scripts.migrate_jiribilla_sections --dry-run`
Expected: a JSON report; the local pages list still shows 13 entries afterwards.

- [ ] **Step 4: Local real run + payload equality**

Capture `build_site_payload(db,'jiribilla')['blocks']` (sorted JSON) before and after; they must be
identical. If not, stop.

- [ ] **Step 5: Deploy and migrate production**

```bash
git push origin main && git push heroku main
curl -s https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla > .prod-before.json
heroku run --no-tty -a latente-cms-core -- python -m scripts.migrate_jiribilla_sections
curl -s https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla > .prod-after.json
```

The `blocks` object must match. Also diff `/delivery/v1/entries?tenant_slug=owa&limit=100` before
and after — it must be unchanged.

- [ ] **Step 6: Rollback (only if the comparison fails)**

Scope the restore to this migration's blocks and demote the containers, otherwise the containers
(higher `Entry.id`) keep winning in `build_site_payload`:

```bash
heroku run --no-tty -a latente-cms-core -- python -c "
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section
from scripts.migrate_jiribilla_sections import CONTAINERS
db = SessionLocal()
t = db.scalar(select(Tenant).where(Tenant.slug=='jiribilla'))
blocks = [b for bs in CONTAINERS.values() for b in bs]
for e, s in db.execute(select(Entry, Section).join(Section, Section.id==Entry.section_id).where(Entry.tenant_id==t.id)).all():
    if s.key in blocks and e.status == 'archived':
        e.status = 'published'
    if s.key in CONTAINERS:
        e.status = 'draft'
db.commit(); print('rolled back')
"
```

Then `git revert` the Task 6 commit and redeploy.

- [ ] **Step 7: Clean up scratch artifacts**

Delete `.prod-before.json`, `.prod-after.json` and any local snapshot files. None belong in git.

---

## Self-Review (revision 2)

**Spec coverage:** site endpoint → Task 1; isolation → Tasks 2, 4, 6; front-end doc → Task 8;
container schemas → Task 3; migration + equality proof → Task 4; inbox merge → Task 5; dashboard
→ Task 6; rollout/rollback → Task 7; nested-repeater risk → Task 0.

**Defects fixed from adversarial review:** the archive/404 blocker is now an explicit phase gate
(and the spec's false claim is corrected); `_JIRIBILLA_INBOX_SECTIONS` stays a dict because
`router.py:423` and `:2711` subscript it; both inbox POST routes take `?form=`;
`tests/test_jiribilla_seed.py` and `tests/test_jiribilla_forms.py` are updated in the tasks that
break them; the migration copies only published sources, seeds missing blocks, strips nested
`__draft`, and sets `published_at`; `rollback()` is guarded by `owns_session`; the isolation test
compares against a committed baseline; the site endpoint is opt-in per tenant; all `$defs` are
hoisted and a test enforces it; the rollback is scoped and demotes containers.

**Accepted, documented behaviour changes:** `privacy_policy`'s bespoke editor branches stop firing
once it is a block (flagged in Task 6 Step 3 for verification); `/sites/` reads `Entry.data` while
the detail endpoint prefers the publish snapshot, so the two can differ for an entry that was
published and then draft-edited — acceptable because `/entries` already behaves this way.
