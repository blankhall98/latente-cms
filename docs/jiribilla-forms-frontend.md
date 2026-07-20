# Jiribilla — Integración de formularios (Front-End)

Guía para conectar los dos formularios del sitio de Jiribilla al CMS de Latente.
Cada envío se guarda en el CMS (visible en el dashboard, sección **Mensajes**) y se
reenvía por correo al equipo de Jiribilla. El front solo necesita hacer los `POST`
descritos aquí.

**Base URL (producción):**

```
https://latente-cms-core-f0bb6db1f7ac.herokuapp.com
```

- No se necesita API key ni autenticación (endpoints públicos).
- CORS está habilitado para cualquier origen, así que funciona igual desde
  `jiribilla.web.app` y desde el dominio nuevo `www.jiribilla.studio` sin cambios.

---

## 1. Formulario de Eventos Privados

```
POST /delivery/v1/jiribilla/eventos-privados
Content-Type: application/json
```

### Campos (todos obligatorios excepto `descripcion`)

| Campo | Tipo | Reglas | Ejemplo |
|---|---|---|---|
| `nombre` | string | 1–160 caracteres | `"Ana García"` |
| `correo` | string | email válido | `"ana@ejemplo.com"` |
| `telefono` | string | 1–64 caracteres | `"+52 55 1234 5678"` |
| `tipo_evento` | string | `"Empresarial"` o `"Personal"` | `"Empresarial"` |
| `fecha` | string | fecha ISO `YYYY-MM-DD` | `"2026-09-15"` |
| `hora` | string | 1–32 caracteres | `"19:00"` |
| `propuesta` | string | `"Em"`, `"Tormenta"`, `"Ultramarinos"`, `"Mtz"` o `"Mixto"` | `"Em"` |
| `num_personas` | number | entero ≥ 1 | `40` |
| `descripcion` | string | opcional, máx. 5000 caracteres | `"Cena corporativa..."` |

### Ejemplo con `fetch`

```js
const res = await fetch(
  "https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/jiribilla/eventos-privados",
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nombre: "Ana García",
      correo: "ana@ejemplo.com",
      telefono: "+52 55 1234 5678",
      tipo_evento: "Empresarial",   // "Empresarial" | "Personal"
      fecha: "2026-09-15",          // YYYY-MM-DD
      hora: "19:00",
      propuesta: "Em",              // "Em" | "Tormenta" | "Ultramarinos" | "Mtz" | "Mixto"
      num_personas: 40,
      descripcion: "Cena corporativa de fin de año.",
    }),
  }
);
const data = await res.json(); // { ok: true, id: 123 }
```

---

## 2. Formulario de Bolsa de Trabajo (con CV)

```
POST /delivery/v1/jiribilla/bolsa-trabajo
Content-Type: multipart/form-data   (usar FormData; NO fijar el header a mano)
```

### Campos (todos obligatorios excepto `respuesta`)

| Campo | Tipo | Reglas | Ejemplo |
|---|---|---|---|
| `nombre` | text | 1–160 caracteres | `"Luis Pérez"` |
| `correo` | text | email válido | `"luis@ejemplo.com"` |
| `telefono` | text | 1–64 caracteres | `"+52 55 8765 4321"` |
| `area_interes` | text | `"Cocina"`, `"Servicio"`, `"Bar"`, `"Administración"` o `"Marketing"` | `"Cocina"` |
| `respuesta` | text | opcional, máx. 5000 caracteres (la pregunta abierta) | `"Me interesa..."` |
| `cv` | file | **PDF real**, máximo **25 MB** | archivo del `<input type="file">` |

El CV se sube a almacenamiento en la nube; el equipo lo recibe como enlace de
descarga en el correo y en el dashboard.

### Ejemplo con `fetch` + `FormData`

```js
const fd = new FormData();
fd.append("nombre", "Luis Pérez");
fd.append("correo", "luis@ejemplo.com");
fd.append("telefono", "+52 55 8765 4321");
fd.append("area_interes", "Cocina"); // "Cocina" | "Servicio" | "Bar" | "Administración" | "Marketing"
fd.append("respuesta", "Me interesa el oficio y la cocina de temporada.");
fd.append("cv", fileInput.files[0]); // <input type="file" accept="application/pdf">

const res = await fetch(
  "https://latente-cms-core-f0bb6db1f7ac.herokuapp.com/delivery/v1/jiribilla/bolsa-trabajo",
  { method: "POST", body: fd } // sin header Content-Type: el navegador pone el boundary
);
const data = await res.json(); // { ok: true, id: 124 }
```

Validación recomendada en el front antes de enviar (el backend la repite de todos modos):

```js
const file = fileInput.files[0];
if (!file) { /* pedir el CV */ }
if (file.type !== "application/pdf") { /* "El CV debe ser un PDF" */ }
if (file.size > 25 * 1024 * 1024) { /* "El CV debe pesar 25 MB o menos" */ }
```

---

## Respuestas

### Éxito (ambos formularios)

```json
{ "ok": true, "id": 123 }
```

HTTP `200`. Muestra el mensaje de confirmación y limpia el formulario.
Nota: aunque el reenvío por correo interno fallara, el mensaje **siempre queda
guardado** en el CMS — para el usuario final el envío fue exitoso.

### Errores

| HTTP | Cuándo | Qué mostrar |
|---|---|---|
| `422` | Falta un campo o es inválido (email mal formado, `num_personas` < 1, CV faltante/vacío, etc.) | "Revisa los campos marcados" — el body incluye `detail` con el detalle por campo |
| `413` | CV mayor a 25 MB | "El CV debe pesar 25 MB o menos" |
| `415` | El archivo no es un PDF real | "El CV debe ser un archivo PDF" |
| `429` | Más de 5 envíos por minuto desde la misma IP | "Demasiados intentos, espera un momento" — deshabilita el botón de enviar mientras hay una petición en curso |
| `503` | Configuración del lado del CMS incompleta (correo destino o almacenamiento) | "No pudimos enviar tu mensaje, intenta más tarde" |
| `404` | Tenant no disponible (no debería ocurrir) | Igual que 503 |

Formato de error: `{ "detail": "..." }` (en `422` el `detail` es una lista de
errores de validación estándar de FastAPI).

---

## Notas

- **Rate limit:** 5 peticiones por minuto por IP en cada endpoint. Deshabilita el
  botón mientras la petición está en vuelo para evitar dobles envíos.
- **Cambio de dominio:** no requiere ningún cambio en esta integración; los
  endpoints funcionan desde cualquier origen.
- **Correos destino:** el equipo de Jiribilla los administra desde el dashboard
  (Site Settings → `eventos_email` y `bolsa_trabajo_email`; si están vacíos se usa
  `contact_email`). El front no maneja ningún correo destino.
- **Dashboard:** los mensajes aparecen en el proyecto Jiribilla, secciones
  **Mensajes: Eventos Privados** y **Mensajes: Bolsa de Trabajo**, con estado de
  leído/no leído y enlace al CV.
