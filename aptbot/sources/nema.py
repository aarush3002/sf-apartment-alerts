"""NEMA (rentnema.com/availabilities): Crescent Heights' own page, server-rendered rows with data-*
attributes including the per-unit securecafe application deep link."""
from __future__ import annotations

import html as htmlmod
import logging
from typing import Any, Optional

from bs4 import BeautifulSoup

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int, parse_money

log = logging.getLogger("aptbot.nema")


def parse_nema(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for it in soup.select("div.availabilities-list__item[data-unit]"):
        try:
            rent = float((it.get("data-rent") or "").replace(",", ""))
        except ValueError:
            rent = parse_money(it.get("data-rent"))
        if rent is not None and rent < 300:  # NEMA uses data-rent="-1" for "CALL FOR DETAILS"
            rent = None
        out.append({
            "unit_number": (it.get("data-unit-raw") or it.get("data-unit") or "").strip(),
            "bedrooms": parse_beds(it.get("data-unit-beds")), "bathrooms": parse_baths(it.get("data-unit-bath")),
            "sqft": parse_int(it.get("data-sqft")), "price": rent,
            "available": fmt_date(it.get("data-availability") or it.get("data-date")),
            "floorplan": it.get("data-floorplan"), "apply": htmlmod.unescape(it.get("data-apply") or "") or None,
            "den": it.get("data-den"), "outdoor": it.get("data-outdoor-space"),
        })
    return out


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    rows = parse_nema(html)
    if not rows and "availabilities-list" not in html:
        raise RuntimeError("NEMA availabilities page did not render the list")
    out = []
    for r in rows:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], sqft=r["sqft"], price=r["price"], available=r["available"],
            listing_url=b.listing_url or cfg["url"], apply_url=r["apply"] or b.apply_url, floorplan=r["floorplan"],
            source="nema", source_id=r["unit_number"], extra={"den": r["den"], "outdoor": r["outdoor"]},
        ))
    return out
