# Jiribilla Tenant Design

## Goal

Add Jiribilla as a new Latente CMS tenant with its own schemas, default content, and bootstrap path while avoiding changes that alter existing tenants.

## Tenant

- Name: `Jiribilla`
- Slug: `jiribilla`
- Contact email: `hola@jiribilla.studio`
- The contact email is seeded into the standard `settings.contact_email` entry and into the Jiribilla footer `mail1` field.
- No default login user is created without an explicit password.

## Scope

Create a self-contained tenant package:

- JSON schemas under `app/schemas/jiribilla/<section>/v1.json`
- Seed content under `content/jiribilla/<section>_v1.json`
- A Jiribilla-specific bootstrap script that creates the tenant, loads schemas, seeds content, and creates published settings
- Tests that validate each Jiribilla content fixture against its schema
- A slug-keyed admin section order for `jiribilla`

Do not modify existing tenant schema files, content files, or behavior except for adding a new `jiribilla` branch where admin ordering is already tenant-slug specific.

## Sections

Jiribilla gets the sections described in `schemas_pdfs/Jiribilla Schemas.pdf`:

- `hero`
- `mesa_uno`
- `proyectos`
- `eventos_privados`
- `glosario`
- `equipo`
- `footer`
- `social_links`
- `forms`

It also gets the standard CMS support sections:

- `settings`
- `privacy_policy`

## Schema Shape

All Jiribilla schemas use the repository's existing JSON Schema 2020-12 style:

- `$schema` and `$id`
- `title`
- `type: object`
- `x-ui.label`
- `x-ui.order`
- `x-ui.textarea` for long text fields
- `x-ui.widget: image` for uploadable image URL fields
- `x-ui.itemTitlePath` for repeatable objects
- `maxItems: 3` for project award icons
- `maxLength: 40` for the two PDF-defined short phrase fields

Image values use the existing object shape:

```json
{
  "url": "",
  "alt": ""
}
```

## Content Defaults

Seed content mirrors the PDF's initial values.

Repeatable content starts empty where the PDF describes CRUD collections:

- `proyectos.projects`
- `glosario.definitions`
- `equipo.gallery`

Uploadable image fields start with empty image objects.

URL fields from "Empty" PDF values start as empty strings.

## Bootstrap

Add an idempotent Jiribilla bootstrap script instead of expanding broad reset scripts:

1. Create or reuse tenant `Jiribilla` with slug `jiribilla`.
2. Load all schemas from `app/schemas/jiribilla`.
3. Seed each content file as a published entry with slug equal to its section key.
4. Create and publish standard `settings` for `hola@jiribilla.studio`.

## Testing

Add a fixture validation test that:

- Asserts every expected Jiribilla schema and content file exists.
- Checks each schema with `Draft202012Validator.check_schema`.
- Validates each content fixture against its matching schema.
- Asserts the known section set remains complete.

## Out Of Scope

- Creating a passworded Jiribilla admin user
- Changing frontend site rendering
- Enabling Firebase uploads by default for all tenants
- Editing ANRO, DEWA, OWA, or Ragni schema/content files
