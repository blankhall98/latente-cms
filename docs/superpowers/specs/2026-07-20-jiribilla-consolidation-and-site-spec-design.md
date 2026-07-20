# Jiribilla — Section Consolidation + Public Site Spec Endpoint

**Date:** 2026-07-20
**Status:** Approved by user (chat) — full consolidation (13 → 4) + generic site payload endpoint

## Goal

Two outcomes, deliberately decoupled so either can ship alone:

1. **Editorial UX** — Jiribilla's dashboard shows 13 nav entries for what is, on the live site,
   a single anchor-navigated page. Consolidate to 4 entries that mirror the real site.
2. **Front-end integration** — a front-end cannot fetch a whole site in one call today
   (delivery items are keyed by numeric `section_id`; the `section_key` never travels in the
   response). Add one public endpoint that returns the whole published site keyed by block.

## Hard constraint (highest priority)

**No behavioural or data change for any other tenant** (ANRO, OWA, DEWA, Ragni-Grady).
This governs every decision below and is verified explicitly (see *Isolation guarantees*).

Concretely this forbids:
- editing `/delivery/v1/contact`, `/delivery/v1/entries`, or the detail delivery endpoint;
- changing `app/services/ui_schema_service.py` behaviour for existing tenants;
- any migration statement not scoped by `tenant_id == jiribilla`;
- removing or renaming the `settings` section key for any tenant.

## Findings that shaped the design

- The live site (`jiribilla.web.app`, moving to `www.jiribilla.studio`) is **one page with anchor
  navigation** (`#mesa-uno`, `#proyectos`, `#catering`, `#equipo`, `#contacto`) — not separate routes.
- Of the 13 dashboard entries, three carry a single text field (`hero`, `mesa_uno`,
  `privacy_policy`) and two carry four scalars (`footer`, `social_links`). Only `proyectos`,
  `equipo`, `glosario` and `eventos_privados` hold substantial content.
- Container sections are an **established pattern in this repo**: DEWA (`about`, `home`), ANRO
  (`home`) and Ragni-Grady (`home`) already bundle several site areas into one section, and the
  page editor renders the top-level blocks as tabs from `x-ui.order`. Jiribilla is the outlier.
- The existing public delivery envelope (`app/schemas/delivery.py`), surfaced to superadmins via
  the **"Delivery JSON"** action in the page editor, is
  `{id, tenant_id, section_id, slug, status, schema_version, data, updated_at, published_at}`.
  The new endpoint follows this convention (snake_case, ISO timestamps) rather than inventing one.
- `/delivery/v1/contact` resolves the recipient by looking up a section whose key is literally
  `settings`, **for every tenant**. Jiribilla must therefore keep a `settings` section.

## Target structure (13 → 4)

| Dashboard entry | Section key | Contents |
|---|---|---|
| Página principal | `pagina_principal` | blocks `hero`, `mesa_uno`, `proyectos`, `eventos_privados`, `glosario`, `equipo`, `forms` |
| Global | `global` | blocks `footer`, `social_links`, `privacy_policy` |
| Mensajes | `mensajes` | the two inboxes as tabs (custom view, not schema-driven) |
| Configuración | `settings` | unchanged section key, relabelled only |

Rationale: *Página principal* is the actual single page; *Global* is the site chrome (on the live
site the footer's quick links **are** the social links, and the privacy link sits in that footer);
*Configuración* keeps the `settings` key for the reason stated above.

### Block schema

`app/schemas/jiribilla/pagina_principal/v1.json` and `.../global/v1.json` are new container
schemas whose top-level properties are the **existing** block schemas, moved verbatim (same
property names, same `$defs.Image`, same repeaters). Root `x-ui.order` lists the blocks in site
order and `x-ui.label` names each tab. The container root carries `"x-ui": {"container": true}`.

## Public site endpoint

```
GET /delivery/v1/sites/{tenant_slug}
```

```json
{
  "tenant": { "slug": "jiribilla", "name": "Jiribilla" },
  "published_at": "2026-07-20T05:33:00Z",
  "blocks": {
    "hero": { "heroText": "..." },
    "proyectos": { "mainText": "...", "projects": [] },
    "footer": { "footerPhrase": "...", "address": "..." },
    "privacy_policy": { "body": "..." }
  }
}
```

Rules:

- Published entries only; drafts and `__draft` keys are stripped (reuse
  `strip_internal_delivery_fields`).
- **Block resolution:** a section whose active schema root has `x-ui.container === true` spreads
  its top-level object properties as blocks; any other section contributes one block under its
  own `section_key`. No existing schema sets the flag, so every current tenant keeps
  one-section-one-block semantics.
- **Excluded keys:** `settings` and any section listed in the inbox map (`mensajes_*`,
  `mensajes`) — internal/administrative, never public.
- `published_at` is the maximum `published_at` across the included entries.
- ETag + `Last-Modified` with `304` support, mirroring `/delivery/v1/entries`.
- Public and unauthenticated, registered under the existing `/delivery/*` prefix so
  `_mark_delivery_routes_public` covers it.
- **Additive only** — a new route in a new module. No existing delivery route is modified.

## Payload stability (the load-bearing property)

The `blocks` keys are exactly today's section keys. Therefore the response is **identical before
and after consolidation**: pre-migration, nine leaf sections each contribute their own key;
post-migration, two container sections spread the same nine keys. Internals change, the contract
does not.

This is what makes the rollout safe given that the front-end already consumes the CMS partially:

1. **Phase 1** — ship `/delivery/v1/sites/{slug}` against today's 13 sections. Nothing breaks;
   the front-end can migrate to the single call whenever it wants.
2. **Phase 2** — consolidate the content model. Invisible to the front-end, including the
   sections it already consumes.

## Migration

`scripts/migrate_jiribilla_sections.py` — idempotent, re-runnable, and **hard-scoped to
`slug == "jiribilla"`** (it aborts if the tenant resolves to anything else):

1. Create `pagina_principal` and `global` sections + schema v1 + published entry if absent.
2. For each source section, copy its published entry `data` into the container entry under the
   block key. Skip blocks already present so re-runs never overwrite edited content.
3. Create a `mensajes` section with a single published entry, whose custom view renders both
   inboxes as tabs selected by `?form=eventos|bolsa`. The stored submissions are **not** touched:
   `JiribillaFormSubmission` rows keep their `form_type`, and `_JIRIBILLA_INBOX_SECTIONS` is
   replaced by a single-section map that resolves `form_type` from the tab instead of the section
   key. Relabel the `settings` section to "Configuración" (name only; the key is untouched).
4. **Retire, never delete**, the source sections: set their entries to `status = "archived"` and
   add the legacy keys to a Jiribilla-guarded exclusion list in the pages query — the same
   mechanism OWA already uses to hide `landing_pages`/`home`. (Note: `Section` has no
   `is_active` column, so retirement is expressed on the `Entry.status` plus the exclusion list.)
   Rollback is un-archiving those entries and reverting the exclusion list.

Prod carries real seeded copy and four labelled test messages; the script must preserve both.

## Isolation guarantees

How "no other project is affected" is *demonstrated*, not asserted:

- Every migration statement filters by the resolved Jiribilla `tenant_id`; the script asserts the
  slug before writing anything.
- Admin router changes stay inside slug-guarded branches (`_JIRIBILLA_*` maps and
  `_section_order_case_for_tenant_slug`), the same mechanism already used for OWA and Ragni.
- `ui_schema_service.py` is **not** modified. If the nested-repeater check (below) shows the
  editor needs work, that work happens in a Jiribilla-guarded branch, and the design is revisited
  before touching shared code.
- Tests: snapshot `/delivery/v1/entries` output for `anro`, `owa`, `dewa`, `ragni-grady` before
  and after migration and assert equality; assert the migration script refuses a non-Jiribilla
  slug; assert each other tenant's section count and keys are unchanged.

## Open technical risk

The page editor builds tabs from `x-ui.order` via `build_sections_ui_fallback_for_object_page`.
That path is proven for DEWA/ANRO/Ragni, but **not** for a nested repeater inside a container —
`proyectos` is an array of projects where each project holds an array of award images. This is
the one place that may need editor work.

**Mitigation:** verify this *first*, with a throwaway container schema in the local DB, before any
migration or schema authoring. If it fails, the fix must stay Jiribilla-scoped or the grouping is
revised (e.g. `proyectos` stays its own page, giving 5 entries instead of 4).

## Testing

- Site endpoint: published-only, `settings`/inbox exclusion, container spreading, leaf fallback,
  ETag/`304`, unknown tenant → 404, inactive tenant → 404.
- **Payload equality**: capture the site payload, run the migration, assert the payload is
  unchanged. This proves the stability contract.
- Migration: idempotency (run twice, same result), non-Jiribilla slug refused, source entries
  archived not deleted, a second run does not overwrite a block edited after the first run.
- Inbox: both tabs resolve the right `form_type`, and the existing toggle-read/delete routes keep
  working against the merged section.
- Cross-tenant regression: the snapshot assertions listed under *Isolation guarantees*.
- The existing suite (57 passing) stays green.

## Out of scope

- Front-end implementation; the integration doc is updated separately once Phase 1 ships.
- Any change to `/delivery/v1/contact`, the Jiribilla form endpoints, or other tenants' content.
- Exposing JSON Schema to the public API (content only, per the approved option).
- Retiring the per-section delivery endpoints — they keep working indefinitely.
