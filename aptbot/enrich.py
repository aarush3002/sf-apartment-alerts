"""Apply-link enrichment: for Yardi/RentCafe buildings whose primary feed lacks per-unit application
links, look the unit up on the securecafe online-leasing page and attach the unit-specific
application URL (oleapplication.aspx?...&UnitID=..&FloorPlanID=..)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .models import Unit, norm_unit
from .sources import securecafe

log = logging.getLogger("aptbot.enrich")
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
CACHE_TTL = 90.0


async def _ole_rows(http: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not cfg.get("property_id"):
        cfg["property_id"] = await securecafe.resolve_property_id(http, cfg)
    url = securecafe.units_url(cfg)
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    html = await http.text(url)
    rows = securecafe.parse_available_units(html)
    _cache[url] = (time.monotonic(), rows)
    return rows


async def enrich_apply_links(http: Any, b: Any, units: list[Unit]) -> int:
    """Mutates units in place; returns number of units that got a unit-specific apply link."""
    cfg = b.apply_enrich
    if not cfg or not units:
        return 0
    try:
        rows = await _ole_rows(http, cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] apply-link enrichment failed: %s", b.id, e)
        return 0
    by_unit = {norm_unit(r["unit_number"]): r for r in rows}
    n = 0
    for u in units:
        r = by_unit.get(norm_unit(u.unit_number))
        if r and r.get("unit_id"):
            u.apply_url = securecafe.apply_url(cfg, r["unit_id"], r["floorplan_id"])
            u.extra["securecafe_unit_id"] = r["unit_id"]
            if u.sqft is None and r.get("sqft"):
                u.sqft = r["sqft"]
            if u.price is None and r.get("price"):
                u.price, u.price_max = r["price"], r.get("price_max")
            if not u.available and r.get("available"):
                u.available = r["available"]
            n += 1
        elif not u.apply_url:
            u.apply_url = f"{securecafe.ole_base(cfg)}/guestlogin.aspx"
    return n
