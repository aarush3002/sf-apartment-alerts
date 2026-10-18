"""Entrata ProspectPortal property sites (e.g. Hanover Soma West).

The /conventional/ floor-plans page embeds every available unit as a JavaScript string:
    var unitsData = '{"<floorplan_id>":[{"unit_number":"161","bedroom":0,...,"available_on":"10\\/22\\/2026",...}]}';
Fields used: marketing_unit_number/unit_number, bedroom, bathroom, sqft, floor_number, available_on,
min_rent/max_rent (or advertised base rent), is_call_for_pricing, floorplan_name.
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import re
from typing import Any, Optional

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int

log = logging.getLogger("aptbot.entrata")
_UNITS = re.compile(r"var\s+unitsData\s*=\s*'(.*?)';", re.S)
_APPLY = re.compile(r'https://[a-z0-9.-]+/[^"\'<> ]*application_authentication[^"\'<> ]*', re.I)


def _money(v: Any) -> Optional[float]:
    try:
        f = float(str(v).replace(",", ""))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def parse_entrata(html: str) -> dict[str, Any]:
    m = _UNITS.search(html)
    if not m:
        raise ValueError("unitsData not found on Entrata page")
    raw = m.group(1)
    try:
        data = json.loads(raw.encode().decode("unicode_escape"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = json.loads(raw.replace('\\"', '"').replace("\\\\/", "/").replace("\\/", "/"))
    rows = []
    for fp_id, units in (data.items() if isinstance(data, dict) else []):
        for u in units or []:
            call = bool(u.get("is_call_for_pricing"))
            lo = None if call else (_money(u.get("min_advertised_base_rent")) or _money(u.get("min_rent_unit")) or _money(u.get("min_rent")))
            hi = None if call else (_money(u.get("max_rent_unit")) or _money(u.get("max_rent")) or lo)
            rows.append({
                "unit_number": str(u.get("marketing_unit_number") or u.get("unit_number") or ""),
                "bedrooms": parse_beds(u.get("bedroom_unit", u.get("bedroom"))),
                "bathrooms": parse_baths(u.get("bathroom_unit", u.get("bathroom"))),
                "sqft": parse_int(u.get("sqft_unit") or u.get("sqft")),
                "floor": str(u["floor_number"]) if u.get("floor_number") not in (None, "") else None,
                "price": lo, "price_max": hi if hi and lo and hi >= lo else lo,
                "available": fmt_date(str(u.get("available_on") or "").replace("\\/", "/")),
                "floorplan": u.get("floorplan_name"), "id": str(u.get("unit_space_id") or u.get("id") or ""),
                "floorplan_id": str(fp_id),
            })
    apply = _APPLY.search(html)
    return {"units": rows, "apply_url": htmlmod.unescape(apply.group(0)) if apply else None}


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    d = parse_entrata(html)
    out = []
    for r in d["units"]:
        if not r["unit_number"]:
            continue
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=b.listing_url or cfg["url"],
            apply_url=cfg.get("apply_url") or d["apply_url"] or b.apply_url or cfg["url"], floorplan=r["floorplan"],
            source="entrata", source_id=r["id"],
        ))
    return out
