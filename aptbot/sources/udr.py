"""UDR (udr.com). The apartments-pricing page embeds
`window.udr.jsonObjPropertyViewModel = {...}` with floorPlans[].units[] and the online-leasing
link template (leaseApplyUrl + pid/realpagesiteid/unitid).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin

from ..models import Unit
from .util import extract_json_after, fmt_date, parse_int

log = logging.getLogger("aptbot.udr")
BASE = "https://www.udr.com"


def parse_udr(html: str) -> dict[str, Any]:
    vm = extract_json_after(html, "window.udr.jsonObjPropertyViewModel =")
    pk = re.search(r"window\.udr\.communityPrimaryKey\s*=\s*(\d+)", html)
    prop_id = pk.group(1) if pk else str(vm.get("propertyId") or "")
    lease = vm.get("leaseApplyUrl") or "/leaseoll/floorplan/"
    rows = []
    for fp in vm.get("floorPlans") or []:
        for u in fp.get("units") or []:
            if u.get("isAvailable") is False:
                continue
            lr = u.get("lowestRent") or {}
            rents = [m.get("Rent") for m in (u.get("rentsMatrix") or []) if isinstance(m.get("Rent"), (int, float))]
            rows.append({
                "unit_number": str(u.get("marketingName") or u.get("lookUpName") or ""),
                "bedrooms": u.get("bedrooms", fp.get("bedRooms")), "bathrooms": u.get("bathrooms", fp.get("bathRooms")),
                "sqft": parse_int(u.get("sqFt")), "floor": str(u.get("floorNumber") or "") or None,
                "price": float(lr["rent"]) if lr.get("rent") else (float(min(rents)) if rents else None),
                "price_max": float(max(rents)) if rents else None,
                "available": max(filter(None, [fmt_date(u.get("AvailableDateLabel")) or fmt_date(u.get("availableDate")),
                                               fmt_date(u.get("earliestMoveInDate"))]), default=None),
                "floorplan": u.get("floorplanName") or fp.get("Name"), "preview": u.get("previewLink"),
                "apartment_id": u.get("apartmentId"), "rp_site": u.get("realpagesiteid"), "rp_unit": u.get("realpageunitid"),
                "term": lr.get("leaseTerm"),
            })
    return {"units": rows, "prop_id": prop_id, "lease_url": lease}


def apply_link(d: dict[str, Any], r: dict[str, Any]) -> str:
    url = f"{urljoin(BASE, d['lease_url'])}?pid={d['prop_id']}"
    if r.get("rp_site"):
        url += f"&realpagesiteid={r['rp_site']}"
    if r.get("rp_unit") is not None:
        url += f"&unitid={r['rp_unit']}&maxDaysToShow=49"
    return url


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    d = parse_udr(html)
    out = []
    for r in d["units"]:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=urljoin(BASE, r["preview"]) if r.get("preview") else cfg["url"],
            apply_url=apply_link(d, r), floorplan=r["floorplan"], source="udr", source_id=str(r.get("apartment_id") or ""),
            extra={"lease_term": r["term"]},
        ))
    return out
