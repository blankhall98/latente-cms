# Jiribilla Forms — Design Spec

**Date:** 2026-07-19
**Status:** Approved by user (chat) — approach A (Jiribilla-scoped, OWA pop-up pattern)

## Goal

Give the Jiribilla site (today `jiribilla.web.app`, soon `www.jiribilla.studio`) working
back-ends for its two public forms — **Eventos Privados** and **Bolsa de Trabajo** — such that
every submission is (1) stored in the CMS, (2) forwarded by email to a per-form official
address editable from the dashboard, and (3) visible in the dashboard in a messages inbox
segmented per form. Zero behavioural change for any other tenant.

## Decisions (confirmed with user)

- **Per-form destination email**: `eventos_email` and `bolsa_trabajo_email` in Jiribilla's
  `settings` entry, each falling back to the existing `contact_email` when blank.
- **CV handling**: the Bolsa de Trabajo PDF (≤ 25 MB) is uploaded to Firebase Storage and
  linked in both the email and the dashboard inbox. It is **not** attached to the email
  (SMTP size limits + base64 inflation make 25 MB attachments bounce).
- **Isolation**: dedicated table, endpoints, and dashboard sections for Jiribilla, following
  the proven OWA pop-up pattern. `/delivery/v1/contact` and all other tenants are untouched.

## Architecture

### 1. Data model — `app/models/jiribilla_forms.py`

`JiribillaFormSubmission` → table `jiribilla_form_submissions`:

| column | type | notes |
|---|---|---|
| `id` | BigInteger PK | autoincrement |
| `tenant_id` | BigInteger FK `tenants.id` ON DELETE CASCADE | indexed |
| `form_type` | String(32) | `eventos_privados` \| `bolsa_trabajo` |
| `name` | String(160) | |
| `email` | String(320) | sender |
| `phone` | String(64) | |
| `data` | JSONB | form-specific fields, Spanish keys, rendered verbatim |
| `cv_url` | String(1024) NULL | bolsa_trabajo only |
| `email_sent` | Boolean default false | forwarding outcome |
| `is_read` | Boolean default false | inbox state |
| `created_at` | DateTime(tz) server_default now() | |

Index: `(tenant_id, form_type, created_at)`.
Alembic migration `add_jiribilla_form_submissions_table`, down_revision `2f7f8c1a9d3a`.

### 2. Public endpoints — `app/api/delivery/jiribilla_forms.py`

Registered in `app/main.py` next to the contact router. No auth (delivery pattern);
per-IP in-memory rate limit (`CONTACT_RATE_LIMIT`/min, same as contact). Both resolve the
`jiribilla` tenant by slug (404 if absent/inactive).

- `POST /delivery/v1/jiribilla/eventos-privados` — JSON body (Pydantic, Spanish keys):
  `nombre`, `correo` (EmailStr), `telefono`, `tipo_evento` (Empresarial|Personal),
  `fecha` (date), `hora`, `propuesta` (Em|Tormenta|Ultramarinos|Mtz|Mixto),
  `num_personas` (int ≥ 1), `descripcion`.
- `POST /delivery/v1/jiribilla/bolsa-trabajo` — `multipart/form-data`:
  `nombre`, `correo`, `telefono`, `area_interes` (Cocina|Servicio|Bar|Administración|Marketing),
  `respuesta` (open question), `cv` (file). CV validation: `%PDF` magic bytes, ≤ 25 MB,
  else 415/413. Uploaded via `upload_file_to_firebase` under `jiribilla/cv/<uuid>.pdf`;
  503 if Firebase is not configured.

**Flow: persist first, email second.** The submission row is committed before any SMTP
call; a mail failure is logged, leaves `email_sent = false`, and still returns
`{"ok": true, "id": ...}` — the message is never lost. Destination address:
form-specific settings key → fallback `contact_email` → 503 if neither is set.
Email reuses `send_contact_email` (generic fields dict; CV link included as a field;
Reply-To = sender).

### 3. Dashboard — segmented inboxes

Two new Jiribilla-only sections: `mensajes_eventos` ("Mensajes: Eventos Privados") and
`mensajes_bolsa` ("Mensajes: Bolsa de Trabajo"), added to
`_JIRIBILLA_SECTION_DASHBOARD_ORDER` and created idempotently by
`scripts/bootstrap_jiribilla.py` (section + minimal schema v1 + published entry, mirroring
`bootstrap_tenant_settings`).

In `app/web/admin/router.py` (same mechanism as OWA `pop_up`):

- `page_edit_get` branches on `section.key in {"mensajes_eventos", "mensajes_bolsa"}` →
  `_jiribilla_inbox_template_response(...)`: queries `JiribillaFormSubmission` for the
  active tenant + mapped `form_type`, newest first; computes total/unread/this-month
  counters; renders `app/templates/admin/jiribilla_inbox.html`.
- Template: endpoint info card, KPI row, message list with unread highlight, expandable
  detail (`<details>`) showing every `data` field, CV download link, per-message
  **toggle read** and **delete** forms.
- New POST routes (session-auth, tenant + section-key guarded, mirroring the OWA delete):
  - `/admin/pages/{entry_id}/jiribilla-messages/{submission_id}/toggle-read`
  - `/admin/pages/{entry_id}/jiribilla-messages/{submission_id}/delete`

### 4. Settings — editable destination emails

`scripts/bootstrap_jiribilla.py` gains an idempotent step that upgrades Jiribilla's
`settings` schema v1 in place: adds `eventos_email` and `bolsa_trabajo_email`
(string/email, Spanish titles) to `properties` if missing, and merges empty defaults into
the existing (published) settings entry without overwriting current values. Editable from
the existing dashboard Settings page.

### 5. Domain change

`www.jiribilla.studio` / `jiribilla.studio` must be added to the `BACKEND_CORS_ORIGINS`
env var on Heroku (keep `jiribilla.web.app` during transition). Documented, not hardcoded.

## Error handling summary

| condition | response |
|---|---|
| rate limit exceeded | 429 |
| tenant missing/inactive | 404 |
| invalid payload | 422 |
| CV not a PDF | 415 |
| CV > 25 MB | 413 |
| Firebase not configured (bolsa) | 503 |
| no destination email configured | 503 |
| SMTP failure | 200 (stored, `email_sent=false`, logged) |

## Testing

`tests/test_jiribilla_forms.py` (existing conftest: real DB, per-test rollback):
happy path + persistence for both forms (mail + storage monkeypatched), email fallback to
`contact_email`, SMTP failure keeps the row, non-PDF and oversized CV rejected, invalid
payload 422, dashboard order includes the new keys. Plus the full existing suite stays green.

## Out of scope

- Any change to `/delivery/v1/contact`, other tenants' flows, or shared email templates.
- Front-end implementation (a separate integration doc is delivered to the FE developer).
- Attachment of the CV to the email.
