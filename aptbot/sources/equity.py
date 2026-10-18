"""Equity Residential (equityapartments.com). The property page embeds
`ea5.unitAvailability = {...}` with BedroomTypes[].AvailableUnits[]; the per-unit online
application link is the Elise redirect built from window.Elise_URL + RmPropId.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from ..models import Unit
from .util import extract_json_after, fmt_date, parse_int

log = logging.getLogger("aptbot.equity")


def parse_equity(html: str) -> dict[str, Any]:
    data = extract_json_after(html, "ea5.unitAvailability =")
    elise = re.search(r"window\.Elise_URL\s*=\s*'([^']+)'", html)
    prop = re.search(r"window\.RmPropId\s*=\s*'([^']+)'", html)
    rows = []
    for bt in data.get("BedroomTypes") or []:
        for u in bt.get("AvailableUnits") or []:
            best = u.get("BestTerm") or {}
            terms = u.get("Terms") or []
            prices = [t.get("Price") for t in terms if isinstance(t.get("Price"), (int, float))]
            price = best.get("Price") or (min(prices) if prices else None)
            rows.append({
                "unit_number": str(u.get("UnitId") or ""), "building_id": str(u.get("BuildingId") or ""),
                "bedrooms": u.get("Bed", bt.get("BedroomCount")), "bathrooms": u.get("Bath"), "sqft": parse_int(u.get("SqFt")),
                "floor": re.sub(r"(?i)^floor\s*", "", str(u.get("Floor") or "")) or None,
                "price": float(price) if price else None, "price_max": float(max(prices)) if prices else None,
                "available": fmt_date(u.get("AvailableDate")), "floorplan": u.get("FloorplanName"),
                "term": best.get("Length"), "exclude_oll": bool(u.get("ExcludeFromOll")),
                "affordable": (u.get("AFFORDABLE") or "N") == "Y",
            })
    return {"units": rows, "elise_url": elise.group(1) if elise else None, "prop_id": prop.group(1) if prop else None}


def apply_link(d: dict[str, Any], r: dict[str, Any]) -> Optional[str]:
    if d.get("elise_url") and d.get("prop_id"):
        return f"{d['elise_url']}{d['prop_id']}&unit_prefix={r['building_id']}&unit_number={quote(r['unit_number'])}"
    return None


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    d = parse_equity(html)
    out = []
    for r in d["units"]:
        if r["affordable"] and not cfg.get("include_affordable"):
            continue
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=b.listing_url or cfg["url"],
            apply_url=apply_link(d, r) or b.apply_url or cfg["url"], floorplan=r["floorplan"], source="equity",
            source_id=r["unit_number"], extra={"lease_term": r["term"]},
        ))
    return out
