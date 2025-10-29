# app/content_registry.py
# Registry declarativo (políticas por Section.key) + soporte opcional para overrides por tenant
from __future__ import annotations
from typing import Literal, TypedDict

EvolutionMode = Literal["additive_only", "custom"]

class SectionMeta(TypedDict, total=False):
    key: str
    label: str
    description: str
    evolution_mode: EvolutionMode
    allow_breaking: bool
    validators_hint: dict

# Base global (aplica a todos los tenants salvo overrides)
REGISTRY_BASE: dict[str, SectionMeta] = {
    "LandingPages": {
        "key": "LandingPages",
        "label": "Landing Pages",
        "description": "Páginas de aterrizaje (monolítico: hero/services/footer u otros)",
        "evolution_mode": "additive_only",
        "allow_breaking": False,
    },
    # Agrega más tipos globales aquí si lo necesitas
}

# Overrides específicos por tenant_id (opcional)
TENANT_OVERRIDES: dict[int, dict[str, SectionMeta]] = {
    # 3: {  # Ejemplo: tenant Latente = 3
    #     "LandingPages": { "description": "Landing Latente (con variantes X)" }
    # }
}

def build_registry_for_tenant(tenant_id: int | None) -> dict[str, SectionMeta]:
    if tenant_id is None:
        return REGISTRY_BASE
    base = REGISTRY_BASE.copy()
    overrides = TENANT_OVERRIDES.get(tenant_id, {})
    out = base.copy()
    for key, meta in overrides.items():
        out[key] = {**base.get(key, {}), **meta}
    return out
