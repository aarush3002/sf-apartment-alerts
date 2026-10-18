"""EliseAI application portal (e.g. eqr-applications.com for Equity Residential).

The public units page https://<host>/building/<slug>/units is a Next.js app that loads
GET /api/searchUnits?building_slug=<slug>&include_price=true, returning every unit the building
currently accepts applications for (bedrooms, sqft, floor, rent, date_available, price matrix).
This is the live application system, so it is at least as current as the marketing site.

Unit numbers come back prefixed with the sub-building ("1-407"); the prefix is stripped so keys
match units already recorded from equityapartments.com ("407").
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlencode

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int

log = logging.getLogger("aptbot.elise")
DEFAULT_HOST = "eqr-applications.com"
_PREFIX = re.compile(r"^([A-Za-z0-9]{1,2})-(.+)$")


def units_page(host: str, slug: str) -> str:
    return f"https://{host}/building/{slug}/units"


def api_url(host: str, slug: str) -> str:
    return f"https://{host}/api/searchUnits?" + urlencode({"building_slug": slug, "include_price": "true"})


def parse_search_units(data: Any) -> list[dict[str, Any]]:
    items = data if isinstance(data, list) else (data or {}).get("units") or []
    out = []
    for u in items:
        raw = str(u.get("unit_number") or "")
        m = _PREFIX.match(raw)
        sub, number = (m.group(1), m.group(2)) if m else (None, raw)
        prices = ((u.get("price_matrix") or {}).get("prices")) or []
        rents = [p["rent"] for p in prices if isinstance(p.get("rent"), (int, float)) and p["rent"] > 0]
        dated = sorted((p for p in prices if p.get("dateAvailable")), key=lambda p: p["dateAvailable"])
        rent = u.get("rent") if isinstance(u.get("rent"), (int, float)) and u["rent"] > 0 else (min(rents) if rents else None)
        term = dated[0].get("leaseTermLength") if dated else None
        out.append({
            "id": u.get("id"), "unit_number": number, "raw_unit": raw, "sub_building": sub or u.get("sub_building_name"),
            "bedrooms": parse_beds(u.get("number_of_bedrooms")), "bathrooms": parse_baths(u.get("number_of_bathrooms")),
            "sqft": parse_int(u.get("square_footage")), "floor": str(u["floor"]) if u.get("floor") is not None else None,
            "price": float(rent) if rent else None, "price_max": float(max(rents)) if rents else None,
            "available": fmt_date(u.get("date_available")) or (fmt_date(dated[0]["dateAvailable"]) if dated else None),
            "floorplan": u.get("floorplan_name"), "stage": u.get("availability_stage"),
            "market_rate": u.get("market_rate_unit") in (1, True, None), "term": term,
        })
    return out


def apply_link(host: str, slug: str, r: dict[str, Any]) -> str:
    q: dict[str, Any] = {"unit_id": r["id"]}
    if r.get("available") and r["available"] != "Now":
        q["move_in_date"] = r["available"]
    if r.get("term"):
        q["lease_term"] = r["term"]
    return units_page(host, slug) + "?" + urlencode(q)


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    host, slug = cfg.get("host", DEFAULT_HOST), cfg["slug"]
    data = await http.json(api_url(host, slug), headers={"Accept": "application/json"})
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected searchUnits response for {slug}: {str(data)[:120]}")
    out = []
    for r in parse_search_units(data):
        if not r["market_rate"] and not cfg.get("include_affordable"):
            continue
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=b.listing_url or units_page(host, slug),
            apply_url=apply_link(host, slug, r), floorplan=r["floorplan"], source="elise", source_id=r["id"],
            extra={"stage": r["stage"], "lease_term": r["term"],
                   **({"display_unit": f"Unit {r['raw_unit']}"} if r["sub_building"] and not str(r["sub_building"]).isdigit() else {})},
        ))
    return out
