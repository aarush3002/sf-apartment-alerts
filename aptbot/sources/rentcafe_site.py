"""RentCafe-hosted property marketing sites (Yardi's "jade"/"aspire" templates, e.g. Presidio
Residences, Towers at Rincon, Trinity's *apts.com sites).

The /floorplans page renders one card per floor plan. Plans with availability show
"Available On: <date>" plus an `a[name=applynow]` link (data-floorplan-name/-size/-sqft/-price)
to a floor-plan detail page that lists the individual units (tr.unit-container rows) with
"Apply Now" links into securecafe online leasing. Plans without availability show "Contact Us".

Config: {type: rentcafe_site, urls: [floorplans page, ...]} (or url:). Several neighborhoods of one
portfolio can share one building entry; each page's label comes from `labels` (url -> name) or the
URL slug.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int, parse_money_range  # noqa: F401

log = logging.getLogger("aptbot.rentcafe_site")


def parse_floorplans(html: str, base: str) -> list[dict[str, Any]]:
    """Floor plans that currently show availability on the /floorplans page (deduped per plan)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    by_detail: dict[str, dict[str, Any]] = {}
    for a in soup.select('a[name="applynow"][data-floorplan-name]'):
        name = a.get("data-floorplan-name", "").strip()
        detail = urljoin(base, a.get("href", ""))
        avail = None
        # The card and its modal both carry the link; the modal has "Available On: <date>" right before it.
        container = a.parent if a.parent is not None else a
        ctx = container.get_text(" ", strip=True)
        m = re.search(r"Available On:\s*(.+?)\s*(?:Availability|Apply|View|$)", ctx)
        if m:
            avail = fmt_date(m.group(1).strip())
        if detail in by_detail:
            if avail and not by_detail[detail].get("available"):
                by_detail[detail]["available"] = avail
            continue
        nums = [float(x) for x in re.findall(r"[\d]+(?:\.\d+)?", a.get("data-floorplan-price") or "")]
        pmin, pmax = (min(nums), max(nums)) if nums else (None, None)
        size = a.get("data-floorplan-size")
        beds = parse_beds(size) if size not in (None, "") else parse_beds(name)
        bm = re.search(r"([\d.]+)\s*ba", name, re.I)
        baths = parse_baths(bm.group(1)) if bm else None
        by_detail[detail] = {
            "floorplan": name, "bedrooms": beds, "bathrooms": baths, "sqft": parse_int(a.get("data-floorplan-sqft")),
            "price": pmin, "price_max": pmax, "available": avail, "detail_url": detail,
        }
        out.append(by_detail[detail])
    return out


def parse_detail(html: str, base: str) -> list[dict[str, Any]]:
    """Unit rows on a floor-plan detail page."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("tr.unit-container"):
        def cell(prefix: str) -> Optional[str]:
            td = tr.find(attrs={"data-selenium-id": re.compile(rf"^{prefix}\d+$")})
            if not td:
                return None
            for lab in td.select(".td-label, .sr-only"):
                lab.decompose()
            return td.get_text(" ", strip=True)
        apt = (cell("Apt") or "").lstrip("#").strip()
        if not apt:
            continue
        pmin, pmax = parse_money_range(cell("Rent"))
        a = tr.find("a", href=True)
        apply = a["href"].strip() if a else None
        if apply and apply.startswith("/"):
            apply = urljoin(base, apply)
        uid = re.search(r"UnitID=(\d+)", apply or "", re.I)
        out.append({"unit_number": apt, "sqft": parse_int(cell("Sqft")), "price": pmin, "price_max": pmax,
                    "available": fmt_date(cell("AvailDate")), "apply_url": apply, "unit_id": uid.group(1) if uid else None})
    return out


_YSI = re.compile(r"ysi\.unitsList\s*=\s*\[")
_OLE_BASE = re.compile(r"https://[a-z0-9.-]+\.securecafe\.com/onlineleasing/[a-z0-9-]+/")


def parse_units_list(html: str) -> list[dict[str, Any]]:
    """RentCafe-hosted pages embed `ysi.unitsList = [...]`: every available unit with beds/baths/sqft/rent/date."""
    m = _YSI.search(html)
    if not m:
        return []
    from .util import extract_json_at
    try:
        items = extract_json_at(html, m.end() - 1, "[", "]")
    except Exception:  # noqa: BLE001
        return []
    ole = _OLE_BASE.search(html)
    base = ole.group(0) if ole else None
    out = []
    for u in items or []:
        if u.get("isCommercial"):
            continue
        uid, fpid, pid = u.get("Id"), u.get("FloorplanId"), u.get("PropertyId")
        apply = None
        if base and uid and fpid and pid:
            apply = f"{base}oleapplication.aspx?stepname=RentalOptions&myOlePropertyId={pid}&FloorPlanID={fpid}&UnitID={uid}"
        out.append({
            "unit_number": str(u.get("UnitCode") or uid or ""), "bedrooms": parse_beds(u.get("Beds")), "bathrooms": parse_baths(u.get("Baths")),
            "sqft": parse_int(u.get("SqFt")), "price": float(u["MinRent"]) if u.get("MinRent") else None,
            "price_max": float(u["MaxRent"]) if u.get("MaxRent") else None, "available": fmt_date(u.get("AvailableDate")),
            "floorplan": u.get("FloorplanName"), "apply_url": apply, "unit_id": str(uid) if uid else None,
        })
    return out


def label_for(url: str, cfg: dict[str, Any], default: str) -> str:
    labels = cfg.get("labels") or {}
    if url in labels:
        return labels[url]
    slug = [p for p in urlsplit(url).path.split("/") if p]
    for part in reversed(slug):
        if part not in ("floorplans", "index", "index.aspx"):
            words = part.replace("-neighborhood", "").replace("-", " ").title()
            return f"{default} – {words}" if cfg.get("prefix_default", True) else words
    return default


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = cfg or b.source
    urls = cfg.get("urls") or [cfg["url"]]
    sem = asyncio.Semaphore(int(cfg.get("concurrency", 3)))
    results: list[Unit] = []
    errors: list[str] = []

    async def one(url: str) -> None:
        async with sem:
            try:
                html = await http.text(url)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {e}")
                return
        if "ysi.unitsList" not in html and 'name="applynow"' not in html and "floorplan-action-button" not in html and "Floor Plan" not in html:
            errors.append(f"{url}: not a RentCafe floorplans page")
            return
        name = label_for(url, cfg, b.name)
        listed = parse_units_list(html)
        if listed:
            for u in listed:
                results.append(Unit(
                    building_id=b.id, building_name=name, unit_number=u["unit_number"], bedrooms=u["bedrooms"],
                    bathrooms=u["bathrooms"], sqft=u["sqft"], price=u["price"], price_max=u["price_max"],
                    available=u["available"], listing_url=url, apply_url=u["apply_url"] or cfg.get("apply_url") or b.apply_url or url,
                    floorplan=u["floorplan"], source="rentcafe_site", source_id=u["unit_id"], extra={"page": url},
                ))
            return
        for fp in parse_floorplans(html, url):
            units: list[dict[str, Any]] = []
            if fp["detail_url"]:
                try:
                    async with sem:
                        dhtml = await http.text(fp["detail_url"])
                    units = parse_detail(dhtml, fp["detail_url"])
                except Exception as e:  # noqa: BLE001
                    log.debug("[%s] detail page failed %s: %s", b.id, fp["detail_url"], e)
            if not units:  # floor-plan-level availability only (typical for single homes)
                units = [{"unit_number": fp["floorplan"], "sqft": fp["sqft"], "price": fp["price"], "price_max": fp["price_max"],
                          "available": fp["available"], "apply_url": None, "unit_id": None}]
            for u in units:
                results.append(Unit(
                    building_id=b.id, building_name=name, unit_number=u["unit_number"], bedrooms=fp["bedrooms"],
                    bathrooms=fp["bathrooms"], sqft=u["sqft"] or fp["sqft"], price=u["price"] or fp["price"],
                    price_max=u["price_max"] or fp["price_max"], available=u["available"] or fp["available"],
                    listing_url=fp["detail_url"] or url, apply_url=u["apply_url"] or cfg.get("apply_url") or b.apply_url or fp["detail_url"],
                    floorplan=fp["floorplan"], source="rentcafe_site", source_id=u.get("unit_id"),
                    extra={"page": url},
                ))

    await asyncio.gather(*(one(u) for u in urls))
    if errors and len(errors) == len(urls):
        raise RuntimeError("; ".join(errors[:3]))
    for e in errors:
        log.warning("[%s] %s", b.id, e)
    return results
