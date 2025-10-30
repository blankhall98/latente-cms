# app/content_registry.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class SectionMeta:
    key: str
    label: str
    evolution_mode: str = "additive_only"  # additive_only | free
    allow_breaking: bool = False
    ui: Dict[str, Any] = None  # hints/widgets por tipo de campo

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "evolution_mode": self.evolution_mode,
            "allow_breaking": self.allow_breaking,
            "ui": self.ui or {},
        }

def build_registry_for_tenant(tenant_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    """
    Registry por tenant. Para OWA/LandingPages v2 habilitamos, temporalmente,
    allow_breaking=True para facilitar el encaje del contenido real.
    """
    # Puedes especializar por tenant_id si lo deseas.
    owa_landing = SectionMeta(
        key="LandingPages",
        label="OWA Landing Pages",
        evolution_mode="additive_only",
        allow_breaking=True,  # <- temporal mientras normalizamos los componentes
        ui={
            "summary_field": "seo.title",
            "widgets": {
                "sections": {"component_mode": "cards"},
            },
        },
    )

    # Devuelve un diccionario con meta por sección
    return {
        "LandingPages": owa_landing.to_dict(),
    }

