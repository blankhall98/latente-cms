# Jiribilla — Integración del contenido con Next.js

Guía para poblar el sitio de Jiribilla desde el CMS de Latente. Todo el contenido publicado se
obtiene en **una sola llamada**, sin autenticación.

Para los dos formularios (Eventos Privados y Bolsa de Trabajo) ver la guía aparte:
[`jiribilla-forms-frontend.md`](./jiribilla-forms-frontend.md).

> ### ⚠️ Lee esto antes de empezar
>
> Si el sitio ya está consumiendo el CMS con los endpoints **por sección**
> (`/delivery/v1/tenants/jiribilla/sections/...`), tienes que migrarlos todos al endpoint único
> que describe esta guía, y **avisar a Latente cuando termines**.
>
> Ese aviso no es un trámite: dispara una reestructuración dentro del CMS que hace que los
> endpoints por sección devuelvan `404`. Si se ejecuta antes de que hayas migrado, las secciones
> que sigan usándolos se quedan sin contenido en producción. Por eso está detenida esperando tu
> confirmación. Los detalles y cómo verificar que ya migraste están en la
> [sección 6](#6-migración-desde-los-endpoints-por-sección).

---

## 1. El endpoint

```
GET https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/sites/jiribilla
```

- Público, sin API key ni cabeceras de autenticación.
- CORS abierto: funciona igual desde `jiribilla.web.app` y desde `www.jiribilla.studio`.
- Devuelve **solo contenido publicado**. Los borradores nunca salen.

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

Diez bloques. **Estas claves están congeladas**: no van a cambiar de nombre.

| Bloque | Campos |
|---|---|
| `hero` | `heroText` |
| `mesa_uno` | `mainText` |
| `proyectos` | `mainText`, `secondaryText`, `tertiaryText`, `projects[]` |
| `eventos_privados` | `mainText`, `secondaryText`, `image1`, `image2`, `image3` |
| `glosario` | `definitions[]` |
| `equipo` | `mainText`, `secondaryText`, `bottomText`, `gallery[]` |
| `forms` | `catering{ mainText, openQuestion }`, `joinOurTeam{ mainText, openQuestion }` |
| `footer` | `footerPhrase`, `address`, `mail1`, `mail2` |
| `social_links` | `substack`, `instagram`, `spotify`, `pressKits` |
| `privacy_policy` | `body` |

Toda imagen tiene la forma `{ "url": string, "alt": string }`.

Los tres arreglos:

- **`proyectos.projects[]`** → `projectName`, `projectDescription`, `projectLink`,
  `projectMainImage` (imagen), `projectSecondaryImage` (imagen), `projectAwards[]` (arreglo de
  imágenes, máximo 3).
- **`glosario.definitions[]`** → `definitionName`, `definitionSymbology`, `definitionText`.
- **`equipo.gallery[]`** → arreglo de imágenes.

### ⚠️ Renderiza a la defensiva

Al momento de escribir esto, **los tres arreglos vienen vacíos** y las URLs de imágenes y redes
sociales están en blanco: el cliente todavía no ha cargado ese contenido en el dashboard. Los
textos sí son reales.

Eso significa que tu código debe tolerar `projects: []`, `gallery: []`, `definitions: []` y
`url: ""` sin romperse. No asumas que hay al menos un proyecto ni que una imagen tiene URL. Si el
componente necesita un mínimo visual, usa un *placeholder* o simplemente no renderices la sección.

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
  blocks: SiteBlocks;
};
```

Nota: `blocks` solo incluye bloques **publicados**. Si el cliente despublica una sección, su clave
desaparece del objeto. Si quieres máxima seguridad en tiempo de ejecución, tipa como
`Partial<SiteBlocks>` y valida antes de renderizar.

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

## 6. Migración desde los endpoints por sección

Si hoy estás llamando a `/delivery/v1/tenants/jiribilla/sections/{seccion}/entries/{seccion}`,
esos endpoints **siguen funcionando por ahora**, así que puedes migrar bloque por bloque sin prisa.

La equivalencia es directa: lo que antes obtenías en el campo `data` de la sección `hero` es ahora
`blocks.hero`. Mismos nombres de campo, misma forma. Solo cambia de dónde lo tomas.

### Por qué esos endpoints van a dejar de funcionar

Dentro del CMS, el sitio de Jiribilla está partido en trece secciones para algo que en realidad es
una sola página. Se van a consolidar en cuatro, para que el cliente no tenga que navegar trece
pantallas al editar.

Al consolidar, las secciones viejas quedan **archivadas** (no se borran: el contenido se conserva).
Pero la API pública solo entrega contenido en estado *publicado*, así que en cuanto se archiven,
`/delivery/v1/tenants/jiribilla/sections/hero/entries/hero` y sus nueve hermanas empiezan a
responder `404`. No es una decisión de diseño que se pueda evitar: es cómo funciona la capa de
entrega.

El endpoint `/sites/jiribilla` **no se ve afectado**: sus claves de bloque son las mismas antes y
después de la consolidación. Por eso es el destino de la migración.

### Qué necesitamos de ti

1. Migra todas las llamadas al endpoint único.
2. Comprueba que ya no queda ninguna llamada por sección. Desde la raíz del proyecto:

   ```bash
   grep -rn "sections/" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" .
   ```

   No debe arrojar ninguna URL del CMS. Revisa también variables de entorno y cualquier URL
   construida por concatenación, que el grep anterior no detecta.
3. **Avisa a Latente** (a quien te compartió este documento) de que el sitio ya lee únicamente
   `/delivery/v1/sites/jiribilla`.

Hasta que llegue ese aviso, la consolidación queda detenida y nada se rompe. Puedes tomarte el
tiempo que necesites.

---

## 7. Errores

| HTTP | Significado | Qué hacer |
|---|---|---|
| `200` | Contenido publicado | Renderizar |
| `304` | Sin cambios desde tu `ETag` | Usar la copia en caché (Next lo maneja solo) |
| `404` | Sitio no encontrado o no habilitado | Revisar la URL; no reintentar en bucle |
| `5xx` | Error del CMS | Servir la última copia buena; el `revalidate` de Next reintenta después |

Como el contenido es contenido y no datos críticos, conviene que un fallo del CMS **no tumbe el
build**. Si usas ISR, Next conserva la última versión generada cuando la revalidación falla.

---

## 8. Resumen

- Una llamada: `GET /delivery/v1/sites/jiribilla`.
- Diez bloques con claves estables; imágenes siempre `{ url, alt }`.
- Arreglos vacíos y URLs en blanco son estados normales hoy: renderiza a la defensiva.
- ISR con `revalidate: 60` es la estrategia recomendada.
- **Si venías usando los endpoints por sección, migra todo y avisa a Latente cuando termines.**
  Ese aviso destraba la consolidación del CMS, que hará que esos endpoints devuelvan `404`.
