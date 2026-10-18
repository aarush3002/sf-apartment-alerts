"""RentCafe-built marketing sites ("jd-fp" floor plan app) embed the full availability JSON in
<script type="application/json" id="jd-fp-data-script-app">. Used by Trinity Place, Etta, Venn,
100/150 Van Ness and many other Yardi properties.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int, parse_money

log = logging.getLogger("aptbot.jdfp")
_SCRIPT = re.compile(r'<script type="application/json" id="jd-fp-data-script-app">(.*?)</script>', re.S)


def parse_jdfp(html: str) -> list[dict[str, Any]]:
    m = _SCRIPT.search(html)
    if not m:
        raise ValueError("jd-fp data script not found")
    d = json.loads(m.group(1))
    out = []
    for u in d.get("units") or []:
        if u.get("type") not in (None, "unit"):
            continue
        pe = u.get("price_entity") or {}
        price = parse_money(u.get("rent_min")) or pe.get("priceLow") or parse_money(u.get("price"))
        pmax = parse_money(u.get("rent_max")) or pe.get("priceHigh")
        avail = fmt_date(u.get("available_date")) if u.get("available_date") else None
        disp = (u.get("available_display") or "").strip()
        if disp.lower().startswith("available now") or disp.lower() == "now":
            avail = "Now"
        elif not avail and disp:
            avail = fmt_date(disp)
        out.append({
            "unit_number": str(u.get("apartment_number") or u.get("title") or "").lstrip("#"),
            "bedrooms": parse_beds(u.get("bedrooms")), "bathrooms": parse_baths(u.get("bathrooms")),
            "sqft": parse_int(u.get("square_feet")), "price": price, "price_max": pmax, "available": avail,
            "floorplan": u.get("floorplan_title"), "permalink": u.get("permalink"), "id": str(u.get("id_value") or u.get("id")),
            "building": u.get("building"), "engrain": u.get("engrain_data") or {},
        })
    return out


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    rows = parse_jdfp(html)
    base = cfg.get("base_url") or cfg["url"]
    units = []
    for r in rows:
        listing = urljoin(base, r["permalink"]) if r.get("permalink") else (b.listing_url or cfg["url"])
        units.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=listing, apply_url=cfg.get("apply_url") or b.apply_url or listing,
            floorplan=r["floorplan"], source="jdfp", source_id=r["id"],
            extra={"floor": (r.get("engrain") or {}).get("floor_name")},
        ))
        if units[-1].extra.get("floor"):
            units[-1].floor = str(units[-1].extra["floor"])
    return units
