# app/web/ui/preview_renderer.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable

# ===================== Modelos para la plantilla =====================

@dataclass
class Button:
    label: str
    href: str
    style: Optional[str] = None
    external: bool = False

# Referencia de modelo; el template recibirá SIEMPRE dicts {"url","alt","type"}
@dataclass
class Media:
    url: str
    alt: Optional[str] = None
    type: Optional[str] = None  # image | video | None

@dataclass
class Card:
    title: Optional[str]
    description: Optional[str]
    image: Optional[str]
    image_alt: Optional[str]
    badges: List[str]
    buttons: List[Button]

@dataclass
class Block:
    kind: str               # "cards" | "kv" | "list" | "table" | "media" | "text" | "json"
    title: Optional[str]
    payload: Any            # list[Card] | dict | list | list[list[str]] | list[dict] | str | dict (json)
    tech_only: bool = False # si True, el template lo ocultará por defecto con el toggle

@dataclass
class SectionRender:
    anchor: str             # id del anchor para TOC
    title: str              # p.ej. "HeroTwoUp" / "Hero" (v3)
    subtitle: Optional[str] # ej. "schema v2" / "schema v3"
    blocks: List[Block]
    raw: Dict[str, Any]     # data cruda de la sección (para panel JSON)

@dataclass
class PreviewPage:
    toc: List[Dict[str, str]]   # [{anchor, label}]
    sections: List[SectionRender]
    meta: Dict[str, Any]

# ===================== Config de filtrado (content-first) =====================

# Campos de “diseño” que no deberían distraer en un preview de contenido
_DESIGN_KEYS = {
    "layout", "theme", "variant", "schema_version", "type",
}

# Bloques con estos títulos se marcan tech_only por consistencia visual
_TECH_TITLES = {"Layout", "Theme", "JSON", "Raw", "Details"}

# ===================== Utils =====================

def _btn(x: Dict[str, Any]) -> Optional[Button]:
    if not isinstance(x, dict):
        return None
    href, label = x.get("href"), x.get("label")
    if isinstance(href, str) and isinstance(label, str):
        return Button(label=label, href=href, style=x.get("style"), external=bool(x.get("external")))
    return None

def _flatten_buttons(*maybe_lists: Any) -> List[Button]:
    out: List[Button] = []
    for src in maybe_lists:
        if isinstance(src, dict):
            b = _btn(src)
            out.extend([b] if b else [])
        elif isinstance(src, list):
            for it in src:
                b = _btn(it)
                out.extend([b] if b else [])
    return out

def _media_list(data: Any) -> List[Dict[str, Any]]:
    """
    Normaliza diferentes entradas de media en una lista de dicts {"url","alt","type"}.
    Nunca retorna objetos ni dataclasses para evitar problemas con 'tojson'.
    """
    res: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and isinstance(it.get("url"), str):
                res.append({
                    "url": it.get("url"),
                    "alt": it.get("alt"),
                    "type": it.get("type"),
                })
    return res

def _kv_table(d: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un dict en algo imprimible; anida dicts/listas como JSON bonito en la plantilla."""
    return d

def _cards_from_items(items: List[Dict[str, Any]]) -> List[Card]:
    cards: List[Card] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        d = it.get("data") if isinstance(it.get("data"), dict) else it

        # imagen
        img, img_alt = None, None
        if isinstance(d.get("image"), str):
            img = d["image"]
        elif isinstance(d.get("images"), list) and d["images"]:
            first = d["images"][0]
            if isinstance(first, dict):
                img = first.get("url")
                img_alt = first.get("alt")
        elif isinstance(d.get("media"), list) and d["media"]:
            first = d["media"][0]
            if isinstance(first, dict):
                img = first.get("url")
                img_alt = first.get("alt")

        badges: List[str] = []
        if isinstance(d.get("indexLabel"), str):
            badges.append(d["indexLabel"])
        if isinstance(d.get("badge"), str):
            badges.append(d["badge"])

        btns = _flatten_buttons(d.get("cta"), d.get("button"), d.get("ctas"), d.get("buttons"), d.get("links"), d.get("actions"))

        title = d.get("title") or it.get("title")
        desc = d.get("description") or it.get("description") or d.get("body")

        if any([title, desc, img, btns, badges]):
            cards.append(Card(
                title=title, description=desc, image=img, image_alt=img_alt, badges=badges, buttons=btns
            ))
    return cards

def _as_list_of_dicts(x: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(x, list) and x and isinstance(x[0], dict):
        return x
    return None

def _add_block(blocks: List[Block], *, kind: str, title: Optional[str], payload: Any, mark_tech: bool = False):
    """Añade un bloque; si mark_tech=True, se marca tech_only para ocultarlo por defecto."""
    tech_only = bool(mark_tech or (title in _TECH_TITLES if title else False))
    blocks.append(Block(kind=kind, title=title, payload=payload, tech_only=tech_only))

# ===================== Renderers v2 por tipo =====================

RendererV2 = Callable[[Dict[str, Any]], List[Block]]

def _render_navbar_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    brand = d.get("brand") if isinstance(d.get("brand"), dict) else None
    if brand:
        _add_block(blocks, kind="kv", title="Brand", payload=_kv_table(brand))
    left = d.get("leftLinks") or d.get("linksLeft")
    right = d.get("rightLinks") or d.get("linksRight")
    if isinstance(left, list) or isinstance(right, list):
        _add_block(blocks, kind="table", title="Left links", payload=left or [])
        _add_block(blocks, kind="table", title="Right links", payload=right or [])
    return blocks

def _render_hero_two_up_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    media = _media_list(d.get("media"))
    overlay = d.get("overlay")
    if media:
        _add_block(blocks, kind="media", title="Media", payload=media)
    if overlay:
        _add_block(blocks, kind="kv", title="Overlay", payload=_kv_table(overlay))
    layout = d.get("layout")
    if layout:
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(layout), mark_tech=True)
    return blocks

def _render_intro_blurb_ctas_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if isinstance(d.get("heading"), str):
        _add_block(blocks, kind="text", title="Heading", payload=d["heading"])
    if isinstance(d.get("body"), str):
        _add_block(blocks, kind="text", title="Body", payload=d["body"])
    ctas = _flatten_buttons(d.get("ctas"))
    if ctas:
        _add_block(blocks, kind="table", title="CTAs", payload=[c.__dict__ for c in ctas])
    if isinstance(d.get("align"), str):
        _add_block(blocks, kind="text", title="Align", payload=d["align"], mark_tech=True)
    return blocks

def _render_discover_grid_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if d.get("title"):
        _add_block(blocks, kind="text", title="Title", payload=d["title"])
    if d.get("intro"):
        _add_block(blocks, kind="text", title="Intro", payload=d["intro"])
    items = _as_list_of_dicts(d.get("items"))
    if items:
        cards = _cards_from_items(items)
        if cards:
            _add_block(blocks, kind="cards", title="Items", payload=[c.__dict__ for c in cards])
        else:
            _add_block(blocks, kind="table", title="Items", payload=items)
    return blocks

def _render_therapies_accordion_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if d.get("title"):
        _add_block(blocks, kind="text", title="Title", payload=d["title"])
    if d.get("intro"):
        _add_block(blocks, kind="text", title="Intro", payload=d["intro"])
    side = d.get("sideImage")
    if isinstance(side, dict) and side.get("url"):
        _add_block(blocks, kind="media", title="Side image", payload=[{
            "url": side.get("url"),
            "alt": side.get("alt"),
            "type": side.get("type"),
        }])
    layout = d.get("layout")
    if layout:
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(layout), mark_tech=True)
    items = _as_list_of_dicts(d.get("items"))
    if items:
        cards: List[Dict[str, Any]] = []
        for it in items:
            btns = _flatten_buttons(it.get("cta"))
            cards.append(Card(
                title=it.get("title"),
                description=it.get("description"),
                image=None, image_alt=None,
                badges=[],
                buttons=btns
            ).__dict__)
            if isinstance(it.get("benefits"), list):
                _add_block(blocks, kind="list", title=f"Benefits · {it.get('title','')}", payload=it["benefits"])
        if cards:
            _add_block(blocks, kind="cards", title="Therapies", payload=cards)
    if isinstance(d.get("bottomCallout"), dict):
        bc = d["bottomCallout"]
        _add_block(blocks, kind="text", title="Bottom callout", payload=bc.get("text", ""))
        btns = _flatten_buttons(bc.get("cta"))
        if btns:
            _add_block(blocks, kind="table", title="Bottom CTA", payload=[b.__dict__ for b in btns])
    return blocks

def _render_memberships_grid_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if d.get("title"):
        _add_block(blocks, kind="text", title="Title", payload=d["title"])
    if d.get("intro"):
        _add_block(blocks, kind="text", title="Intro", payload=d["intro"])
    items = _as_list_of_dicts(d.get("items"))
    if items:
        cards = _cards_from_items(items)
        if cards:
            _add_block(blocks, kind="cards", title="Plans", payload=[c.__dict__ for c in cards])
        else:
            _add_block(blocks, kind="table", title="Items", payload=items)
    return blocks

def _render_hero_media_overlay_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    media = _media_list(d.get("media"))
    if media:
        _add_block(blocks, kind="media", title="Media", payload=media)
    overlay = d.get("overlay")
    if overlay:
        _add_block(blocks, kind="kv", title="Overlay", payload=_kv_table(overlay))
    return blocks

def _render_social_gallery_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if d.get("heading"):
        _add_block(blocks, kind="text", title="Heading", payload=d["heading"])
    info = {}
    for k in ("handle", "profileUrl", "source"):
        if d.get(k):
            info[k] = d[k]
    if info:
        _add_block(blocks, kind="kv", title="Info", payload=_kv_table(info))
    imgs = _media_list(d.get("images"))
    if imgs:
        _add_block(blocks, kind="media", title="Images", payload=imgs)
    return blocks

def _render_footer_v2(d: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if d.get("title"):
        _add_block(blocks, kind="text", title="Title", payload=d["title"])
    if isinstance(d.get("contact"), dict):
        _add_block(blocks, kind="kv", title="Contact", payload=_kv_table(d["contact"]))
    groups = _as_list_of_dicts(d.get("groups"))
    if groups:
        _add_block(blocks, kind="table", title="Groups", payload=groups)
    if isinstance(d.get("newsletter"), dict):
        _add_block(blocks, kind="kv", title="Newsletter", payload=_kv_table(d["newsletter"]))
    if isinstance(d.get("bottomBar"), dict):
        _add_block(blocks, kind="kv", title="Bottom bar", payload=_kv_table(d["bottomBar"]))
    return blocks

# Registry extensible por type (v2)
RENDERERS_V2: Dict[str, RendererV2] = {
    "NavBar": _render_navbar_v2,
    "HeroTwoUp": _render_hero_two_up_v2,
    "IntroBlurbCTAs": _render_intro_blurb_ctas_v2,
    "DiscoverGrid": _render_discover_grid_v2,
    "TherapiesAccordion": _render_therapies_accordion_v2,
    "MembershipsGrid": _render_memberships_grid_v2,
    "HeroMediaOverlay": _render_hero_media_overlay_v2,
    "SocialGallery": _render_social_gallery_v2,
    "Footer": _render_footer_v2,
}

# ===================== Renderers v3 =====================

def _render_v3_hero(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    media = _media_list(base.get("media") or d.get("media"))
    if media:
        _add_block(blocks, kind="media", title="Media", payload=media)
    head_bits = {}
    for k in ("eyebrow", "heading", "subheading", "richText", "variant"):
        if base.get(k):
            head_bits[k] = base[k]
    if d.get("rotatingWords") is not None:
        head_bits["rotatingWords"] = d.get("rotatingWords")
    if d.get("scrimOpacity") is not None:
        head_bits["scrimOpacity"] = d.get("scrimOpacity")
    if head_bits:
        _add_block(blocks, kind="kv", title="Hero", payload=_kv_table(head_bits))
    acts = _flatten_buttons(base.get("actions"))
    if acts:
        _add_block(blocks, kind="table", title="Actions", payload=[a.__dict__ for a in acts])
    # Diseño → tech-only
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

def _render_v3_intro(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if base.get("eyebrow"):
        _add_block(blocks, kind="text", title="Eyebrow", payload=base["eyebrow"])
    if base.get("heading"):
        _add_block(blocks, kind="text", title="Heading", payload=base["heading"])
    if base.get("subheading"):
        _add_block(blocks, kind="text", title="Subheading", payload=base["subheading"])
    if base.get("richText"):
        _add_block(blocks, kind="text", title="Body", payload=base["richText"])
    acts = _flatten_buttons(base.get("actions"))
    if acts:
        _add_block(blocks, kind="table", title="Actions", payload=[a.__dict__ for a in acts])
    # Diseño
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

def _render_v3_grid(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    items = _as_list_of_dicts((d or {}).get("items"))
    if base.get("heading"):
        _add_block(blocks, kind="text", title="Heading", payload=base["heading"])
    if base.get("subheading"):
        _add_block(blocks, kind="text", title="Subheading", payload=base["subheading"])
    if items:
        cards = _cards_from_items(items)  # ya soporta image/media/cta/buttons/actions
        if cards:
            _add_block(blocks, kind="cards", title="Items", payload=[c.__dict__ for c in cards])
        else:
            _add_block(blocks, kind="table", title="Items", payload=items)
    else:
        _add_block(blocks, kind="text", title="Items", payload="(No items yet)")
    # Diseño
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

def _render_v3_accordion(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if base.get("heading"):
        _add_block(blocks, kind="text", title="Heading", payload=base["heading"])
    if base.get("richText"):
        _add_block(blocks, kind="text", title="Intro", payload=base["richText"])
    side = d.get("sideImage")
    if isinstance(side, dict) and side.get("url"):
        _add_block(blocks, kind="media", title="Side image", payload=[{
            "url": side.get("url"),
            "alt": side.get("alt"),
            "type": side.get("type"),
        }])
    items = _as_list_of_dicts(d.get("items"))
    if items:
        for it in items:
            title = it.get("title", "")
            if it.get("description"):
                _add_block(blocks, kind="text", title=f"{title} · Description", payload=it["description"])
            bullets = it.get("bullets")
            if isinstance(bullets, list):
                _add_block(blocks, kind="list", title=f"{title} · Bullets", payload=bullets)
            acts = _flatten_buttons(it.get("actions"))
            if acts:
                _add_block(blocks, kind="table", title=f"{title} · Actions", payload=[a.__dict__ for a in acts])
    else:
        _add_block(blocks, kind="text", title="Items", payload="(No items yet)")
    # Diseño
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

def _render_v3_plans(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    if base.get("heading"):
        _add_block(blocks, kind="text", title="Heading", payload=base["heading"])
    items = _as_list_of_dicts(d.get("items"))
    if items:
        cards: List[Card] = []
        for it in items:
            badge = it.get("badge")
            btns  = _flatten_buttons(it.get("actions"))
            cards.append(Card(
                title=it.get("title"),
                description=it.get("description"),
                image=it.get("image"),
                image_alt=it.get("imageAlt"),
                badges=[badge] if badge else [],
                buttons=btns
            ))
        _add_block(blocks, kind="cards", title="Plans", payload=[c.__dict__ for c in cards])
    else:
        _add_block(blocks, kind="text", title="Plans", payload="(No items yet)")
    # Diseño
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

def _render_v3_hero_overlay(d: Dict[str, Any], base: Dict[str, Any]) -> List[Block]:
    blocks: List[Block] = []
    media = _media_list(base.get("media") or d.get("media"))
    if media:
        _add_block(blocks, kind="media", title="Media", payload=media)
    overlay_bits = {}
    if (d or {}).get("rotatingWords"):
        overlay_bits["rotatingWords"] = d["rotatingWords"]
    if (d or {}).get("scrimOpacity") is not None:
        overlay_bits["scrimOpacity"] = d["scrimOpacity"]
    if overlay_bits:
        _add_block(blocks, kind="kv", title="Overlay", payload=_kv_table(overlay_bits))
    # Diseño
    if base.get("layout"):
        _add_block(blocks, kind="kv", title="Layout", payload=_kv_table(base["layout"]), mark_tech=True)
    if base.get("theme"):
        _add_block(blocks, kind="kv", title="Theme", payload=_kv_table(base["theme"]), mark_tech=True)
    return blocks

# Dispatch v3
def _renderer_v3_for(sec_type: Optional[str]):
    return {
        "Hero": _render_v3_hero,
        "Intro": _render_v3_intro,
        "Grid": _render_v3_grid,
        "Accordion": _render_v3_accordion,
        "Plans": _render_v3_plans,
        "HeroOverlay": _render_v3_hero_overlay,
        # Estos tres pueden venir igual desde v2; reusamos v2 renderer:
        "NavBar": lambda d, base: _render_navbar_v2(d),
        "SocialGallery": lambda d, base: _render_social_gallery_v2(d),
        "Footer": lambda d, base: _render_footer_v2(d),
    }.get(sec_type)

# ===================== Fallbacks y construcción de página =====================

def _generic_blocks(d: Dict[str, Any]) -> List[Block]:
    """Nunca pierde info: muestra media, arrays, dicts, texto y JSON. Filtra llaves de diseño del KV."""
    blocks: List[Block] = []
    # media
    for key in ("media", "images"):
        ms = _media_list(d.get(key))
        if ms:
            _add_block(blocks, kind="media", title=key.title(), payload=ms)
    # arrays “items/links/ctas/…” como tabla
    for key in ("items", "links", "ctas", "buttons", "actions"):
        val = d.get(key)
        if isinstance(val, list):
            _add_block(blocks, kind="table", title=key.title(), payload=val)
    # resto KV (sin diseño)
    kv = {k: v for k, v in d.items() if k not in ("media", "images", "items", "links", "ctas", "buttons", "actions") and k not in _DESIGN_KEYS}
    if kv:
        _add_block(blocks, kind="kv", title="Details", payload=_kv_table(kv))
    return blocks or [Block(kind="json", title="Raw", payload=d, tech_only=True)]

def _section_title(sec: Dict[str, Any]) -> str:
    t = sec.get("type") or "Section"
    return str(t)

def _section_subtitle(sec: Dict[str, Any]) -> str:
    sv = sec.get("schema_version")
    return f"schema v{sv}" if isinstance(sv, int) else ""

def _build_from_sections(data: Dict[str, Any]) -> PreviewPage:
    """Renderer basado en data.sections. Soporta v2 (OWA) y v3 (design-first)."""
    sections = data.get("sections") or []

    rendered: List[SectionRender] = []
    toc: List[Dict[str, str]] = []

    for idx, sec in enumerate(sections, 1):
        if not isinstance(sec, dict):
            continue

        sec_type = sec.get("type")
        sec_data = sec.get("data") if isinstance(sec.get("data"), dict) else {}
        title = _section_title(sec)
        subtitle = _section_subtitle(sec)
        anchor = f"sec-{idx}-{title}".lower().replace(" ", "-")

        # v3 explícito por schema_version
        if sec.get("schema_version") == 3:
            renderer = _renderer_v3_for(sec_type)
            if renderer:
                blocks = renderer(sec_data, sec)  # pasa base completo para leer campos base
            else:
                blocks = _generic_blocks(sec_data)
        else:
            # v2 (o desconocido) → intenta registry v2 y cae a genérico
            renderer_v2 = RENDERERS_V2.get(sec_type)
            blocks = renderer_v2(sec_data) if renderer_v2 else _generic_blocks(sec_data)

        # JSON crudo al final (tech-only en la plantilla)
        _add_block(blocks, kind="json", title="JSON", payload=sec, mark_tech=True)

        rendered.append(SectionRender(
            anchor=anchor, title=title, subtitle=subtitle, blocks=blocks, raw=sec
        ))
        toc.append({"anchor": anchor, "label": f"{idx}. {title}"})

    meta = {
        "replace": bool(data.get("replace")),
        "seo": data.get("seo") if isinstance(data.get("seo"), dict) else None
    }
    return PreviewPage(toc=toc, sections=rendered, meta=meta)

def build_render_model(ui_hints: List[Dict[str, Any]], data: Dict[str, Any]) -> PreviewPage:
    """
    Punto único para el router. Si hay 'sections' en data (como OWA), renderiza por secciones.
    """
    if isinstance(data, dict) and isinstance(data.get("sections"), list):
        return _build_from_sections(data)

    # Fallback: no hay 'sections'. Intento básico con ui_hints → un único panel KV + JSON.
    blocks: List[Block] = []
    if ui_hints:
        flat: Dict[str, Any] = {}
        for f in ui_hints:
            name = f.get("name")
            path = f.get("path") or []
            # resolver valor
            cur = data
            for p in path:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    cur = None
                    break
            if name:
                flat[name] = cur
        if flat:
            _add_block(blocks, kind="kv", title="Fields", payload=flat)
    if isinstance(data, dict):
        _add_block(blocks, kind="json", title="JSON", payload=data, mark_tech=True)

    section = SectionRender(anchor="sec-1-generic", title="Content", subtitle=None, blocks=blocks, raw=data or {})
    return PreviewPage(toc=[{"anchor": section.anchor, "label": "1. Content"}], sections=[section], meta={})

# Alias para compatibilidad con plantillas antiguas que pudieron importar build_preview
def build_preview(entry: Dict[str, Any]) -> PreviewPage:
    data = entry.get("data") or {}
    return _build_from_sections(data)



# === Edit UI: sintetizar ui_hints desde entry.data (v2/v3 con sections) ===

def synthesize_ui_hints_from_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Genera ui_hints 'on the fly' a partir de entry.data.
    - Soporta data['sections'] (v2/v3). Extrae campos simples en cada sección.
    - Devuelve una lista de hints: {name,label,section,path,type,format?,widget?,enum?}
    Paths son absolutos desde la raíz de entry.data para que entry_edit.html los resuelva.
    """
    hints: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return hints

    sections = data.get("sections")
    if not isinstance(sections, list):
        # Fallback plano: para data sin 'sections', exponer claves simples de primer nivel
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) or (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                hints.append({
                    "name": k,
                    "label": k.replace("_", " ").title(),
                    "section": "General",
                    "path": [k],
                    "type": "boolean" if isinstance(v, bool) else
                            "number" if isinstance(v, (int, float)) else
                            "array" if isinstance(v, list) else
                            "string",
                    "format": "textarea" if isinstance(v, str) and len(v) > 120 else None,
                })
        return hints

    def _is_simple(x: Any) -> bool:
        return isinstance(x, (str, int, float, bool)) or (isinstance(x, list) and all(isinstance(i, str) for i in x))

    for idx, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        sec_type = sec.get("type") or f"Section {idx+1}"
        base_path = ["sections", idx]  # raíz de la sección

        # Campos en el "base" de la sección (v3) que sean simples
        for k, v in list(sec.items()):
            if k in ("data", "schema_version", "type"):
                continue
            if _is_simple(v):
                hints.append({
                    "name": f"{sec_type}:{k}",
                    "label": k.replace("_", " ").title(),
                    "section": sec_type,
                    "path": base_path + [k],
                    "type": "boolean" if isinstance(v, bool) else
                            "number" if isinstance(v, (int, float)) else
                            "array" if isinstance(v, list) else
                            "string",
                    "format": "textarea" if isinstance(v, str) and len(v) > 120 else None,
                })

        d = sec.get("data")
        if not isinstance(d, dict):
            continue

        # Campos simples dentro de data
        for k, v in d.items():
            # Heurística de imagen
            is_image_key = str(k).lower() in ("image", "heroimage", "cover", "thumbnail")
            if isinstance(v, dict) and isinstance(v.get("url"), str):
                # Dict de media {url, alt, type}
                hints.append({
                    "name": f"{sec_type}:{k}",
                    "label": k.replace("_", " ").title(),
                    "section": sec_type,
                    "path": base_path + ["data", k, "url"],
                    "type": "string",
                    "widget": "image",
                })
                # Alt opcional
                if isinstance(v.get("alt"), str):
                    hints.append({
                        "name": f"{sec_type}:{k}_alt",
                        "label": f"{k} alt".title(),
                        "section": sec_type,
                        "path": base_path + ["data", k, "alt"],
                        "type": "string",
                    })
                continue

            if _is_simple(v):
                hints.append({
                    "name": f"{sec_type}:{k}",
                    "label": k.replace("_", " ").title(),
                    "section": sec_type,
                    "path": base_path + ["data", k],
                    "type": "boolean" if isinstance(v, bool) else
                            "number" if isinstance(v, (int, float)) else
                            "array" if isinstance(v, list) else
                            "string",
                    "format": "textarea" if isinstance(v, str) and (len(v) > 120 or k in ("description","richText","body","intro","subheading")) else None,
                    "widget": "image" if (isinstance(v, str) and (is_image_key or v.lower().startswith(("http://","https://")) and (".png" in v or ".jpg" in v or ".jpeg" in v or ".webp" in v))) else None,
                })
            # NOTA: arrays/dicts complejos se editan en el "JSON avanzado" de la plantilla

    return hints





