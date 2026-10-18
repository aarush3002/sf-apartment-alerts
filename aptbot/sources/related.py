"""Related Rentals (relatedrentals.com). Property pages server-render one `div.unit-card` per
available unit (Drupal), each linking to a unit detail page. Related does not expose an online
application link; the unit page carries the tour/contact form, so it doubles as the apply link.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_money

log = logging.getLogger("aptbot.related")


def parse_related(html: str, base: str = "https://www.relatedrentals.com") -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("div.unit-card article.node--type-unit"):
        api_id = card.get("data-api-id") or card.get("data-nid")
        a = card.find("a", href=True)
        href = urljoin(base, a["href"]) if a else base
        title_el = card.select_one("p.title")
        title = title_el.get_text(" ", strip=True) if title_el else (card.get("data-gtm-name") or "")
        price_el = card.select_one(".unit-price__value")
        price = parse_money(price_el.get_text(strip=True)) if price_el else parse_money(card.get("data-price"))
        avail_el = card.select_one(".unit-availability__value")
        avail = fmt_date(avail_el.get_text(strip=True)) if avail_el else fmt_date(card.get("data-dimension9"))
        beds = parse_beds(card.get("data-dimension6"))
        baths = parse_baths(card.get("data-dimension7"))
        if beds is None:
            beds = parse_beds(title)
        out.append({
            "unit_number": str(api_id or ""), "title": title, "bedrooms": beds, "bathrooms": baths,
            "price": price, "available": avail, "url": href, "brand": card.get("data-brand"),
        })
    return out


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    html = await http.text(cfg["url"])
    rows = parse_related(html)
    if not rows and "unit-card" not in html and "Available Apartments" not in html:
        raise RuntimeError("Related page did not look like a property page")
    out = []
    for r in rows:
        out.append(Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"], bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"], price=r["price"], available=r["available"], listing_url=r["url"],
            apply_url=cfg.get("apply_url") or b.apply_url or r["url"], floorplan=r["title"], source="related",
            source_id=r["unit_number"], extra={"display_unit": r["title"] or f"Listing {r['unit_number']}"},
        ))
    return out
