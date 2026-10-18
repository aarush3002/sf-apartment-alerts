"""Greystar property pages (greystar.com/<slug>/p_<id>) are Next.js; the RSC flight payload embeds
`propertyUnits` (available units w/ price matrix) and `floorplans` (id -> bedroom count).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..models import Unit
from .util import decode_next_flight, extract_json_after, fmt_date, parse_int

log = logging.getLogger("aptbot.greystar")


def parse_greystar(html: str) -> dict[str, Any]:
    flight = decode_next_flight(html)
    if '"propertyUnits"' not in flight:
        raise ValueError("propertyUnits not found in Greystar page")
    units = extract_json_after(flight, '"propertyUnits":', "[", "]")
    fps: dict[str, dict[str, Any]] = {}
    if '"floorplans":' in flight:
        try:
            for fp in extract_json_after(flight, '"floorplans":', "[", "]"):
                fps[str(fp.get("id"))] = fp
        except ValueError:
            pass
    lease = re.search(r'"leaseOnlineUrl":"([^"]+)"', flight)
    rows = []
    for u in units:
        fp = fps.get(str(u.get("floorPlanId")), {})
        rows.append({
            "unit_number": str(u.get("unitNumber") or ""), "bedrooms": fp.get("bedroomCount"),
            "bathrooms": fp.get("bathroomCount"), "sqft": parse_int(u.get("area")),
            "floor": u.get("floorShortLabel") or u.get("floorLabel"), "price": u.get("minPrice"),
            "price_max": u.get("maxPrice"), "available": fmt_date(u.get("availableOn")),
            "floorplan": u.get("floorPlanLabel") or fp.get("label"), "id": str(u.get("unitId") or ""),
        })
    return {"units": rows, "lease_url": lease.group(1) if lease else None}


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    d = parse_greystar(html)
    out = []
    for r in d["units"]:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=str(r["floor"]) if r["floor"] else None,
            price=float(r["price"]) if r["price"] else None, price_max=float(r["price_max"]) if r["price_max"] else None,
            available=r["available"], listing_url=b.listing_url or cfg["url"],
            apply_url=cfg.get("apply_url") or d["lease_url"] or b.apply_url, floorplan=r["floorplan"],
            source="greystar", source_id=r["id"],
        ))
    return out
