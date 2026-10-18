from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def norm_unit(s: str) -> str:
    """Normalize a unit number for matching across sources: '#0219' -> '219', 'APT 0243' -> '243', '22E' -> '22E'."""
    s = (s or "").upper()
    s = re.sub(r"^(UNIT|APT|APARTMENT|HOME|RESIDENCE|#)\s*", "", s.strip())
    s = re.sub(r"[^A-Z0-9]", "", s)
    s = s.lstrip("0") or s
    return s


@dataclass
class Unit:
    building_id: str
    building_name: str
    unit_number: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    floor: Optional[str] = None
    price: Optional[float] = None
    price_max: Optional[float] = None
    available: Optional[str] = None  # human-readable date or "Now"
    listing_url: str = ""
    apply_url: Optional[str] = None
    floorplan: Optional[str] = None
    source: str = ""
    source_id: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.building_id}:{norm_unit(self.unit_number)}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["key"] = self.key
        return d

    def short(self) -> str:
        bd = "?" if self.bedrooms is None else ("Studio" if self.bedrooms == 0 else f"{self.bedrooms}bd")
        pr = f"${self.price:,.0f}" if self.price else "?"
        return f"{self.building_name} #{self.unit_number} {bd} {pr} avail {self.available or '?'}"
