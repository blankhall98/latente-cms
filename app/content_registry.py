from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Literal


EvolutionMode = Literal["additive_only", "free"]


@dataclass
class SectionMeta:
    key: str
    label: str
    # "additive_only": solo cambios compatibles (agregar campos, marcar opcionales, etc.)
    # "free": se permiten cambios que rompen compatibilidad (para fases de encaje)
    evolution_mode: EvolutionMode = "additive_only"
    allow_breaking: bool = False
    # Hints/widgets por tipo de campo o secciones. Usar default_factory para evitar dicts compartidos.
    ui: Dict[str, Any] = field(default_factory=dict)

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
    Registry por tenant.
    Para OWA/LandingPages podemos permitir temporalmente allow_breaking=True mientras
    normalizamos componentes y contenido real. En producción estable, volver a False.
    """
    owa_landing = SectionMeta(
        key="LandingPages",
        label="OWA Landing Pages",
        evolution_mode="additive_only",
        allow_breaking=True,  # ← temporal; revertir a False cuando el schema se congele
        ui={
            # Campo de resumen para listados del Admin (breadcrumbs, grids, etc.)
            "summary_field": "seo.title",
            # Preferencias de UI para el editor schema-driven
            "widgets": {
                "sections": {"component_mode": "cards"},  # UX de bloques como tarjetas
            },
        },
    )

    # Nota: Si en el futuro diferenciamos por tenant_id, podemos ramificar aquí.
    # p.ej. if tenant_id == TENANT_ANRO_ID: return {...} con metas específicas.

    return {
        "LandingPages": owa_landing.to_dict(),
    }


