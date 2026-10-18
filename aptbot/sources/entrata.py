"""Entrata ProspectPortal property sites (e.g. Hanover Soma West).

Entrata embeds its availability in the page as JSON. Two shapes have been seen in the wild:

* current: `"floorplanUnits":{"propertyUnits":[{...}]}` with `unitNumber`, `bedroom`, `bathroom`,
  `sqft`, `availableOn`, `minAdvertisedBaseRent`, `floorplanName`, `floor_number`.
* older: an HTML-escaped JSON blob in a `data-units="..."` attribute using snake_case keys
  (`available_on`, `unit_number`, `min_advertised_base_rent`).

Both are handled. Applications are started from the site's
`/Apartments/module/application_authentication/...` link, which is property-wide rather than
per-unit, so every unit shares it.
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import re
from typing import Any, Optional

from ..models import Unit
from .util import extract_json_after, fmt_date, parse_baths, parse_beds, parse_int, parse_money

log = logging.getLogger("aptbot.entrata")

_APPLY = re.compile(r"https?://[^\"'<> ]*?/Apartments/module/application_authentication/[^\"'<> ]*", re.I)
_DATA_UNITS = re.compile(r'data-units="([^"]+)"')


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return None


def _row(u: dict[str, Any]) -> dict[str, Any]:
    price = parse_money(_first(u, "minAdvertisedBaseRent", "min_advertised_base_rent", "minRent", "min_rent"))
    pmax = parse_money(_first(u, "maxAdvertisedBaseRent", "max_advertised_base_rent", "maxRent", "max_rent"))
    floor = _first(u, "floor_number", "floorNumber", "floor")
    amen = u.get("amenities") or []
    return {
        "unit_number": str(_first(u, "marketingUnitNumber", "marketing_unit_number", "unitNumber", "unit_number") or ""),
        "bedrooms": parse_beds(_first(u, "bedroom", "bedrooms", "bed")),
        "bathrooms": parse_baths(_first(u, "bathroom", "bathrooms", "bath")),
        "sqft": parse_int(u.get("sqft")),
        "floor": str(floor) if floor not in (None, "") else None,
        "price": price, "price_max": pmax if pmax and price and pmax > price else None,
        "available": fmt_date(_first(u, "availableOn", "available_on", "availableDate")),
        "floorplan": _first(u, "floorplanName", "floorplan_name"),
        "id": str(_first(u, "unitId", "unit_id", "id") or ""),
        "amenities": [a.get("name") for a in amen if isinstance(a, dict) and a.get("name")][:6],
    }


def parse_entrata(html: str) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    if '"propertyUnits"' in html:
        try:
            units = extract_json_after(html, '"propertyUnits":', "[", "]") or []
        except ValueError:
            units = []
    if not units:  # older template
        for m in _DATA_UNITS.finditer(html):
            try:
                blob = json.loads(htmlmod.unescape(m.group(1)))
            except json.JSONDecodeError:
                continue
            if isinstance(blob, dict):
                blob = blob.get("units") or blob.get("propertyUnits") or []
            if isinstance(blob, list):
                units.extend(x for x in blob if isinstance(x, dict))
    if not units:
        raise ValueError("no Entrata unit JSON found (looked for propertyUnits and data-units)")
    rows, seen = [], set()
    for u in units:
        if not isinstance(u, dict):
            continue
        r = _row(u)
        key = r["id"] or r["unit_number"]
        if not r["unit_number"] or key in seen:
            continue
        seen.add(key)
        rows.append(r)
    apply_m = _APPLY.search(html)
    return {"units": rows, "apply_url": apply_m.group(0) if apply_m else None}


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    d = parse_entrata(html)
    out = []
    for r in d["units"]:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=b.listing_url or cfg["url"],
            apply_url=cfg.get("apply_url") or b.apply_url or d["apply_url"] or cfg["url"],
            floorplan=r["floorplan"], source="entrata", source_id=r["id"],
            extra={"amenities": r["amenities"]} if r["amenities"] else {},
        ))
    return out
