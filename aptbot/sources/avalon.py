"""AvalonBay (avaloncommunities.com). Community pages embed a `"units":[...]` JSON array with
every available apartment (unitName, floorNumber, bedroomNumber, squareFeet, pricing, url).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..models import Unit
from .util import extract_json_after, fmt_date, parse_int

log = logging.getLogger("aptbot.avalon")


def parse_avalon(html: str) -> list[dict[str, Any]]:
    if '"units":[{"unitId"' not in html:
        if '"units":[]' in html and "AVB-" in html:
            return []  # community page rendered with zero vacancies
        raise ValueError('marker \'"units":[{"unitId"\' not found')  # unexpected page; keep units, back off
    units = extract_json_after(html, '"units":[{"unitId"', "[", "]")
    out = []
    for u in units:
        pr = ((u.get("startingAtPricesUnfurnished") or {}).get("prices") or {})
        price = pr.get("netEffectivePrice") or pr.get("price")
        out.append({
            "unit_number": str(u.get("unitName") or u.get("unitId") or ""), "bedrooms": u.get("bedroomNumber"),
            "bathrooms": u.get("bathroomNumber"), "sqft": parse_int(u.get("squareFeet")), "floor": str(u.get("floorNumber") or "") or None,
            "price": float(price) if price else None, "price_max": float(pr["totalPrice"]) if pr.get("totalPrice") else None,
            "available": fmt_date(u.get("availableDateUnfurnished")), "floorplan": (u.get("floorPlan") or {}).get("name"),
            "url": u.get("url"), "id": u.get("unitId"), "status": u.get("unitStatus"), "furnish": u.get("furnishStatus"),
        })
    return out


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    rows = parse_avalon(html)
    out = []
    for r in rows:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=r["url"] or cfg["url"], apply_url=cfg.get("apply_url") or b.apply_url or r["url"],
            floorplan=r["floorplan"], source="avalon", source_id=r["id"], extra={"status": r["status"]},
        ))
    return out
