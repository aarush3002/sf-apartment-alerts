from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


@dataclass
class Filters:
    bedrooms: list[int] = field(default_factory=lambda: [2, 3])
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    min_sqft: Optional[int] = None
    available_by: Optional[str] = None  # ISO date; only alert if available on/before this date
    include_unknown_bedrooms: bool = False

    def merged(self, override: Optional[dict[str, Any]]) -> "Filters":
        if not override:
            return self
        d = {**self.__dict__, **{k: v for k, v in override.items() if k in self.__dict__}}
        return Filters(**d)


@dataclass
class Building:
    id: str
    name: str
    source: dict[str, Any]
    address: str = ""
    stories: Optional[int] = None
    enabled: bool = True
    listing_url: str = ""
    apply_url: Optional[str] = None
    interval: Optional[int] = None
    fallback: Optional[dict[str, Any]] = None
    apply_enrich: Optional[dict[str, Any]] = None
    filters: Optional[dict[str, Any]] = None
    sub_buildings: Optional[dict[str, str]] = None
    building_type: Optional[str] = None  # "high-rise" | "mid-rise" | "low-rise"; derived from `stories` if omitted
    notes: str = ""

    @property
    def classification(self) -> str:
        if self.building_type:
            return self.building_type
        if self.stories is None:
            return "unknown height"
        if self.stories >= 12:
            return "high-rise"
        if self.stories >= 5:
            return "mid-rise"
        return "low-rise"

    @property
    def classification_label(self) -> str:
        c = self.classification
        if c == "unknown height":
            return "height unknown"
        return f"{c.capitalize()} ({self.stories} floors)" if self.stories else c.capitalize()

    @property
    def raw_sub_buildings(self) -> Optional[dict[str, str]]:
        return self.sub_buildings

    @property
    def kind(self) -> str:
        return self.source["type"]


@dataclass
class Config:
    buildings: list[Building]
    filters: Filters
    notify: dict[str, Any]
    poll: dict[str, Any]
    paths: dict[str, Path]
    raw: dict[str, Any]

    def building(self, bid: str) -> Building:
        for b in self.buildings:
            if b.id == bid:
                return b
        raise KeyError(bid)

    @property
    def enabled_buildings(self) -> list[Building]:
        return [b for b in self.buildings if b.enabled]


def load_config(path: Optional[Path | str] = None) -> Config:
    load_dotenv(ROOT / ".env")
    p = Path(path) if path else Path(os.environ.get("APTBOT_CONFIG", DEFAULT_CONFIG))
    raw = yaml.safe_load(p.read_text()) or {}
    filters = Filters(**{k: v for k, v in (raw.get("filters") or {}).items() if k in Filters.__dataclass_fields__})
    buildings = []
    for bid, b in (raw.get("buildings") or {}).items():
        b = dict(b)
        buildings.append(Building(id=bid, **{k: v for k, v in b.items() if k in Building.__dataclass_fields__ and k != "id"}))
    data_dir = Path(os.environ.get("APTBOT_DATA_DIR", raw.get("data_dir") or (ROOT / "data")))
    paths = {"data": data_dir, "db": data_dir / "aptbot.sqlite", "status": data_dir / "status.json",
             "current": data_dir / "current_matches.md", "logs": ROOT / "logs"}
    poll = {"interval": 60, "securecafe_interval": 120, "jitter": 0.15, "concurrency": 6, "per_host": 2,
            "relist_days": 21, **(raw.get("poll") or {})}
    return Config(buildings=buildings, filters=filters, notify=raw.get("notify") or {}, poll=poll, paths=paths, raw=raw)
