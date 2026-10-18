"""SightMap (Engrain) interactive-map JSON, used as the availability widget by many buildings.

Config: {type: sightmap, embed: <hash>}. The public embed page reveals the JSON API URL
(https://sightmap.com/app/api/v1/<key>/sightmaps/<id>); `units` there contains only currently
available units with unit_number, price, available_on, area, floor and floor_plan (bedrooms).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..models import Unit
from .util import fmt_date, parse_int

log = logging.getLogger("aptbot.sightmap")
_API = re.compile(r"https://sightmap\.com/app/api/v1/[a-z0-9]+/sightmaps/\d+")
_api_cache: dict[str, str] = {}


def embed_url(embed: str) -> str:
    return f"https://sightmap.com/embed/{embed}"


def find_api_url(embed_html: str) -> Optional[str]:
    m = _API.search(embed_html)
    return m.group(0) if m else None


def parse_sightmap(data: dict[str, Any]) -> list[dict[str, Any]]:
    d = data.get("data", data)
    fps = {str(f["id"]): f for f in d.get("floor_plans") or []}
    floors = {str(f["id"]): f for f in d.get("floors") or []}
    out = []
    for u in d.get("units") or []:
        fp = fps.get(str(u.get("floor_plan_id")), {})
        fl = floors.get(str(u.get("floor_id")), {})
        floor = fl.get("filter_short_label") or fl.get("filter_label")
        unit_no = u.get("unit_number") or u.get("label") or ""
        price = u.get("price")
        if price is None:
            price = u.get("min_price")
        out.append({
            "unit_number": str(unit_no), "bedrooms": fp.get("bedroom_count"), "bathrooms": fp.get("bathroom_count"),
            "floorplan": fp.get("name"), "sqft": parse_int(u.get("area")), "floor": str(floor) if floor else None,
            "price": float(price) if isinstance(price, (int, float)) else None,
            "price_max": float(u["max_price"]) if isinstance(u.get("max_price"), (int, float)) else None,
            "available": fmt_date(u.get("available_on")) or (u.get("display_available_on") or None),
            "building": u.get("building"), "id": str(u.get("id")),
            "affordable": bool((u.get("affordable_housing_info") or {}).get("is_affordable_housing_unit")),
            "outbound_links": u.get("outbound_links") or [],
        })
    return out


async def get_api_url(http: Any, embed: str) -> str:
    if embed in _api_cache:
        return _api_cache[embed]
    html = await http.text(embed_url(embed))
    api = find_api_url(html)
    if not api:
        raise RuntimeError(f"could not find SightMap API url in embed {embed}")
    _api_cache[embed] = api
    return api


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    api = cfg.get("api_url") or await get_api_url(http, cfg["embed"])
    try:
        data = await http.json(api)
    except Exception:
        _api_cache.pop(cfg.get("embed", ""), None)  # api key may rotate; re-resolve next time
        raise
    rows = parse_sightmap(data)
    units = []
    for r in rows:
        if r.get("affordable") and not cfg.get("include_affordable"):
            continue
        name = b.name
        apply = cfg.get("apply_url") or b.apply_url
        for l in r["outbound_links"] or []:
            if isinstance(l, dict) and "apply" in (l.get("label") or "").lower() and l.get("url"):
                apply = l["url"]
        units.append(Unit(
            building_id=b.id, building_name=name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], floor=r["floor"], price=r["price"], price_max=r["price_max"],
            available=r["available"], listing_url=b.listing_url or f"{embed_url(cfg['embed'])}?unit_number={r['unit_number']}",
            apply_url=apply, floorplan=r["floorplan"], source="sightmap", source_id=r["id"],
            extra={"sightmap_unit_link": f"{embed_url(cfg['embed'])}?unit_number={r['unit_number']}"},
        ))
    return units
