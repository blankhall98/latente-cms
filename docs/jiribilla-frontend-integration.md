# Jiribilla — Integración del contenido con Next.js

Guía para poblar el sitio de Jiribilla desde el CMS de Latente. Todo el contenido publicado se
obtiene en **una sola llamada**, sin autenticación.

Para los dos formularios (Eventos Privados y Bolsa de Trabajo) ver la guía aparte:
[`jiribilla-forms-frontend.md`](./jiribilla-forms-frontend.md).

*Contenido verificado contra el endpoint en producción el 20 de julio de 2026.*

> ### ⚠️ Lee esto antes de empezar
>
> Según nuestros registros de servidor, el sitio hoy consume del CMS **una sola sección**:
> `social_links`, por el endpoint viejo. Ese endpoint —y los otros nueve por sección— **van a
> dejar de funcionar** cuando reestructuremos el CMS.
>
> Es un cambio de una llamada, no una migración grande: está resuelto paso a paso en la
> [sección 6](#6-tu-caso-migrar-social_links). Cuando lo hagas, **avisa a Latente**. Hasta que
> llegue ese aviso la reestructuración está detenida y nada se rompe, así que puedes tomarte el
> tiempo que necesites.
>
> Si detectas que el sitio consume alguna otra sección que no vimos en los registros, dinos cuál.

---

## 1. El endpoint

```
GET https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla
```

- Público, sin API key ni cabeceras de autenticación.
- CORS abierto: funciona igual desde `jiribilla.web.app` y desde `www.jiribilla.studio`.
- Devuelve **solo contenido publicado**. Los borradores nunca salen.

Pruébalo antes de escribir código:

```bash
curl -s https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla | jq
```

### Forma de la respuesta

```json
{
  "tenant":  { "slug": "jiribilla", "name": "Jiribilla" },
  "published_at": "2026-07-20T05:33:32+00:00",
  "blocks": { "hero": { }, "mesa_uno": { } }
}
```

Todo lo que necesitas está en `blocks`. `published_at` es la fecha de publicación más reciente de
todo el sitio — útil si quieres mostrar "actualizado el…" o invalidar caché manualmente.

---

## 2. Contrato de `blocks`

Diez bloques, uno por zona del sitio. **Estas claves están congeladas**: no van a cambiar de
nombre, ni siquiera cuando reestructuremos el CMS por dentro.

| Bloque | Zona del sitio | Campos |
|---|---|---|
| `hero` | encabezado | `heroText` |
| `mesa_uno` | `#mesa-uno` | `mainText` |
| `proyectos` | `#proyectos` | `mainText`, `secondaryText`, `tertiaryText`, `projects[]` |
| `eventos_privados` | `#catering` | `mainText`, `secondaryText`, `image1`, `image2`, `image3` |
| `glosario` | glosario | `definitions[]` |
| `equipo` | `#equipo` | `mainText`, `secondaryText`, `bottomText`, `gallery[]` |
| `forms` | textos de los 2 formularios | `catering{ mainText, openQuestion }`, `joinOurTeam{ mainText, openQuestion }` |
| `footer` | pie | `footerPhrase`, `address`, `mail1`, `mail2` |
| `social_links` | pie / nav | `substack`, `instagram`, `spotify`, `pressKits` |
| `privacy_policy` | aviso de privacidad | `body` |

Toda imagen tiene la forma `{ "url": string, "alt": string }`.

Los tres arreglos:

- **`proyectos.projects[]`** → `projectName`, `projectDescription`, `projectLink`,
  `projectMainImage` (imagen), `projectSecondaryImage` (imagen), `projectAwards[]` (arreglo de
  imágenes, máximo 3).
- **`glosario.definitions[]`** → `definitionName`, `definitionSymbology`, `definitionText`.
- **`equipo.gallery[]`** → arreglo de imágenes.

`blocks.forms` son los **textos** que acompañan a los formularios (el encabezado y la pregunta
abierta de cada uno). El envío de esos formularios va por endpoints aparte, documentados en
[`jiribilla-forms-frontend.md`](./jiribilla-forms-frontend.md).

### ⚠️ Qué viene vacío hoy — renderiza a la defensiva

Los textos ya son reales. Pero al 20 de julio de 2026 estos campos **están vacíos en el CMS**
porque el cliente todavía no los ha cargado desde el dashboard:

| Campo | Estado |
|---|---|
| `proyectos.projects` | arreglo vacío |
| `glosario.definitions` | arreglo vacío |
| `equipo.gallery` | arreglo vacío |
| `eventos_privados.image1/2/3` | `url` y `alt` en blanco |
| `social_links.substack/instagram/spotify/pressKits` | los cuatro en blanco |
| `privacy_policy.body` | en blanco |

Dos consecuencias prácticas:

**Tu código debe tolerarlo sin romperse.** No asumas que hay al menos un proyecto, ni que una
imagen tiene URL. Si un componente necesita un mínimo visual, usa un *placeholder* o no
renderices la sección.

**Ojo con `social_links` en particular:** hoy los cuatro vienen en blanco. Si el sitio ya los está
leyendo del CMS y aun así se ven los enlaces de Substack y Spotify, es que hay valores de respaldo
en el código. Conviene que lo confirmes y decidas cuál manda — y avisarle al cliente que llene
esos campos en el dashboard.

---

## 3. Tipos de TypeScript

```ts
// types/site.ts
export type CmsImage = { url: string; alt: string };

export type Project = {
  projectName: string;
  projectDescription: string;
  projectLink: string;
  projectMainImage: CmsImage;
  projectSecondaryImage: CmsImage;
  projectAwards: CmsImage[];
};

export type Definition = {
  definitionName: string;
  definitionSymbology: string;
  definitionText: string;
};

export type FormCopy = { mainText: string; openQuestion: string };

export type SiteBlocks = {
  hero: { heroText: string };
  mesa_uno: { mainText: string };
  proyectos: {
    mainText: string;
    secondaryText: string;
    tertiaryText: string;
    projects: Project[];
  };
  eventos_privados: {
    mainText: string;
    secondaryText: string;
    image1: CmsImage;
    image2: CmsImage;
    image3: CmsImage;
  };
  glosario: { definitions: Definition[] };
  equipo: {
    mainText: string;
    secondaryText: string;
    bottomText: string;
    gallery: CmsImage[];
  };
  forms: { catering: FormCopy; joinOurTeam: FormCopy };
  footer: { footerPhrase: string; address: string; mail1: string; mail2: string };
  social_links: { substack: string; instagram: string; spotify: string; pressKits: string };
  privacy_policy: { body: string };
};

export type SitePayload = {
  tenant: { slug: string; name: string };
  published_at: string | null;
  blocks: Partial<SiteBlocks>;
};
```

`blocks` va tipado como `Partial` a propósito: solo incluye bloques **publicados**. Si el cliente
despublica una sección desde el dashboard, su clave desaparece del objeto. Por eso los ejemplos de
abajo usan `?.` en todos lados.

---

## 4. El fetcher

```ts
// lib/cms.ts
import type { SitePayload } from "@/types/site";

const CMS_BASE = process.env.NEXT_PUBLIC_CMS_URL
  ?? "https://latente-cms-core-f0bb6db1f7ac.herokuapp.com";

export async function getSite(): Promise<SitePayload> {
  const res = await fetch(`${CMS_BASE}/delivery/v1/sites/jiribilla`, {
    // ISR: la página se regenera como mucho una vez por minuto.
    next: { revalidate: 60 },
  });

  if (!res.ok) {
    throw new Error(`CMS respondió ${res.status}`);
  }
  return res.json();
}
```

### Estrategias de caché

El endpoint ya envía cabeceras correctas, verificadas en producción:

```
Cache-Control: public, max-age=60, s-maxage=120, stale-while-revalidate=120
ETag: 368c636d0ee6...
Last-Modified: Mon, 20 Jul 2026 05:33:32 GMT
```

Y responde `304 Not Modified` ante `If-None-Match`. Tres opciones según lo que necesites:

```ts
// A) ISR — recomendado para este sitio. Rápido y se actualiza solo.
fetch(url, { next: { revalidate: 60 } });

// B) Siempre fresco (SSR en cada request). Úsalo solo si el cliente necesita ver
//    cambios al instante; sacrifica el CDN.
fetch(url, { cache: "no-store" });

// C) Estático con revalidación bajo demanda: combínalo con un webhook o
//    revalidatePath('/') cuando el cliente publique.
fetch(url, { next: { revalidate: false, tags: ["site"] } });
```

Para el uso normal, **la opción A es la correcta**. Un minuto de retraso tras publicar es
aceptable y mantiene el sitio rápido.

---

## 5. Ejemplo de uso

```tsx
// app/page.tsx
import { getSite } from "@/lib/cms";

export default async function Home() {
  const { blocks } = await getSite();

  return (
    <main>
      <section id="hero">
        <h1>{blocks.hero?.heroText}</h1>
      </section>

      <section id="proyectos">
        <p>{blocks.proyectos?.mainText}</p>

        {/* El arreglo puede venir vacío: no asumas que hay proyectos. */}
        {blocks.proyectos?.projects?.length ? (
          <ul>
            {blocks.proyectos.projects.map((p) => (
              <li key={p.projectName}>
                {p.projectMainImage?.url && (
                  <img src={p.projectMainImage.url} alt={p.projectMainImage.alt || ""} />
                )}
                <h3>{p.projectName}</h3>
                <p>{p.projectDescription}</p>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </main>
  );
}
```

Si usas `next/image` con las imágenes del CMS, agrega el host de Firebase Storage a
`next.config.js`:

```js
// next.config.js
module.exports = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "firebasestorage.googleapis.com" },
    ],
  },
};
```

---

## 6. Tu caso: migrar `social_links`

En los registros del servidor, la única sección que el sitio pide hoy al CMS es `social_links`,
por el endpoint viejo, con un parámetro anti-caché (`?_=…`) — o sea, un `fetch` desde el
navegador.

### Antes

```ts
const res = await fetch(
  `${CMS_BASE}/delivery/v1/tenants/jiribilla/sections/social_links/entries/social_links?_=${Date.now()}`
);
const { data } = await res.json();
// data.substack, data.instagram, data.spotify, data.pressKits
```

### Después

```ts
import { getSite } from "@/lib/cms";

const { blocks } = await getSite();
const social = blocks.social_links;
// social?.substack, social?.instagram, social?.spotify, social?.pressKits
```

Los nombres de los campos son **idénticos**. Lo que antes venía en `data` ahora viene en
`blocks.social_links`. Si prefieres tocar lo mínimo:

```ts
const { blocks } = await getSite();
const data = blocks.social_links ?? {};   // el resto de tu componente no cambia
```

Recuerda que hoy los cuatro valores vienen en blanco (ver sección 2), así que conserva tus
respaldos hasta que el cliente los llene en el dashboard.

### Verifica que no quede ninguna otra llamada

Desde la raíz del proyecto:

```bash
grep -rn "delivery/v1/tenants" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" .
```

No debe arrojar nada. Revisa también variables de entorno y URLs armadas por concatenación, que
ese grep no detecta.

### Luego, avísale a Latente

A quien te compartió este documento, diciendo que el sitio ya lee únicamente
`/delivery/v1/sites/jiribilla`. Ese aviso destraba la reestructuración del CMS.

---

## 7. Por qué los endpoints por sección van a dejar de funcionar

Contexto, para que la petición anterior tenga sentido.

Dentro del CMS, el sitio de Jiribilla está partido en trece secciones para algo que en realidad es
una sola página. Se van a consolidar en cuatro, para que el cliente no navegue trece pantallas
cada vez que quiere editar un texto.

Al consolidar, las secciones viejas quedan **archivadas** — no se borran, el contenido se
conserva. Pero la API pública solo entrega contenido en estado *publicado*, así que en cuanto se
archiven, `/delivery/v1/tenants/jiribilla/sections/social_links/entries/social_links` y sus nueve
hermanas empiezan a responder `404`. No es una decisión de diseño evitable: es cómo funciona la
capa de entrega.

`/sites/jiribilla` **no se ve afectado**: sus claves de bloque son las mismas antes y después de
la consolidación. Por eso es el destino de la migración, y por eso está publicado desde antes de
tocar nada.

---

## 8. Errores

| HTTP | Significado | Qué hacer |
|---|---|---|
| `200` | Contenido publicado | Renderizar |
| `304` | Sin cambios desde tu `ETag` | Usar la copia en caché (Next lo maneja solo) |
| `404` | Sitio no encontrado o no habilitado | Revisar la URL; no reintentar en bucle |
| `5xx` | Error del CMS | Servir la última copia buena; el `revalidate` de Next reintenta después |

Como el contenido es contenido y no datos críticos, conviene que un fallo del CMS **no tumbe el
build**. Si usas ISR, Next conserva la última versión generada cuando la revalidación falla.

---

## 9. Resumen

- Una sola llamada: `GET /delivery/v1/sites/jiribilla`.
- Diez bloques con claves estables; imágenes siempre `{ url, alt }`.
- Varios campos vienen vacíos hoy (arreglos, imágenes, redes, aviso de privacidad): renderiza a la
  defensiva y conserva respaldos.
- ISR con `revalidate: 60` es la estrategia recomendada.
- **Acción concreta:** mueve la llamada de `social_links` al endpoint único y avisa a Latente.
  Ese aviso destraba la reestructuración del CMS, que hará que los endpoints por sección
  devuelvan `404`.
