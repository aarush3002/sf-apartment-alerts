"""Yardi RentCafe online-leasing (securecafe.com) availability page.

URL: https://{host}/onlineleasing/{slug}/availableunits.aspx?myOlePropertyId={property_id}
Lists every currently available unit grouped by floor plan, with a per-unit "Apply" action
(rentaloptions.aspx?UnitID=..&FloorPlanID=..). This is the live Yardi Voyager data that the
building's own site is fed from, so it is never stale relative to the property site.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from ..models import Unit
from .util import fmt_date, parse_baths, parse_beds, parse_int, parse_money_range

log = logging.getLogger("aptbot.securecafe")

_CAPTION = re.compile(r"Floor Plan:\s*(?P<name>.+?)\s*-\s*(?P<beds>[\d.]+)\s*Bed", re.I)
_BATHS = re.compile(r"([\d.]+)\s*Bath", re.I)
_ACTION = re.compile(r"UnitID=(\d+)&FloorPlanID=(\d+)", re.I)
_PHONE = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")


def ole_base(cfg: dict[str, Any]) -> str:
    return f"https://{cfg['host']}/onlineleasing/{cfg['slug']}"


def units_url(cfg: dict[str, Any]) -> str:
    return f"{ole_base(cfg)}/availableunits.aspx?myOlePropertyId={cfg['property_id']}"


def apply_url(cfg: dict[str, Any], unit_id: str, floorplan_id: str) -> str:
    return (f"{ole_base(cfg)}/oleapplication.aspx?stepname=RentalOptions&myOlePropertyId={cfg['property_id']}"
            f"&FloorPlanID={floorplan_id}&UnitID={unit_id}&header=1")


def parse_available_units(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    phone_m = _PHONE.search(html)
    phone = phone_m.group(0) if phone_m else None
    out: list[dict[str, Any]] = []
    for table in soup.select("table.availableUnits"):
        cap = table.find("caption")
        cap_text = cap.get_text(" ", strip=True) if cap else ""
        m = _CAPTION.search(cap_text)
        fp_name = m.group("name").strip() if m else None
        beds = parse_beds(m.group("beds")) if m else None
        bm = _BATHS.search(cap_text)
        baths = parse_baths(bm.group(1)) if bm else None
        for tr in table.select("tr.AvailUnitRow"):
            cells = {}
            for td in tr.find_all(["td", "th"]):
                label = (td.get("data-label") or "").strip().lower()
                # strip sr-only helper text
                for sr in td.select(".sr-only"):
                    sr.decompose()
                cells[label] = td.get_text(" ", strip=True)
            unit_no = cells.get("apartment") or ""
            unit_no = re.sub(r"^#", "", unit_no).strip()
            if not unit_no:
                continue
            sqft = parse_int(cells.get("sq.ft.") or cells.get("sq. ft."))
            pmin, pmax = parse_money_range(cells.get("rent"))
            avail = fmt_date(cells.get("date available") or cells.get("availability"))
            btn = tr.find("input", class_="UnitSelect") or tr.find("input", attrs={"onclick": True})
            uid = fpid = None
            contact_only = False
            if btn is not None:
                label = (btn.get("value") or "").strip().lower()
                onclick = btn.get("onclick", "")
                # Property disabled online leasing for this unit: rent shows "Call", button says Contact
                if (btn.get("data-function") == "contactUsLink" or btn.get("data-norm") == "1"
                        or label.startswith("contact") or "contactusdialog" in onclick.lower()):
                    contact_only = True
                    dm = re.search(r"myFloorPlanId=(\d+)", onclick)
                    if dm:
                        fpid = dm.group(1)
                    um = re.search(r"myUnitId=(\d+)", onclick)
                    if um:
                        uid = um.group(1)
            if btn is not None and not contact_only:
                am = _ACTION.search(btn.get("onclick", ""))
                if am:
                    uid, fpid = am.group(1), am.group(2)
                dm = re.search(r"MoveInDate=([\d/]+)", btn.get("onclick", ""))
                if dm and not avail:
                    avail = fmt_date(dm.group(1))
            out.append({
                "unit_number": unit_no, "floorplan": fp_name, "bedrooms": beds, "bathrooms": baths,
                "sqft": sqft, "price": pmin, "price_max": pmax, "available": avail,
                "unit_id": uid, "floorplan_id": fpid, "contact_only": contact_only, "phone": phone,
            })
    return out


def resolve_property_id_from_html(html: str) -> Optional[str]:
    m = re.search(r"myOlePropertyId=(\d+)", html, re.I)
    return m.group(1) if m else None


async def resolve_property_id(http: Any, cfg: dict[str, Any]) -> str:
    """Fetch the guestlogin page and pull the numeric myOlePropertyId from it."""
    html = await http.text(f"{ole_base(cfg)}/guestlogin.aspx")
    pid = resolve_property_id_from_html(html)
    if not pid:
        raise RuntimeError(f"could not resolve securecafe property id for {cfg['host']}/{cfg['slug']}")
    return pid


def contact_url(cfg: dict[str, Any], r: dict[str, Any]) -> str:
    """Yardi opens a Contact Us dialog for units with no published price."""
    q = f"?myPropertyId={cfg['property_id']}&isThemedSite=1&isDialog=1"
    if r.get("unit_id"):
        q += f"&myUnitId={r['unit_id']}"
    if r.get("floorplan_id"):
        q += f"&myFloorPlanId={r['floorplan_id']}"
    return f"{ole_base(cfg)}/contactusdialog.aspx{q}"


def to_units(rows: list[dict[str, Any]], cfg: dict[str, Any], b: Any) -> list[Unit]:
    units = []
    for r in rows:
        u = Unit(
            building_id=b.id, building_name=b.name, unit_number=r["unit_number"],
            bedrooms=r["bedrooms"], bathrooms=r["bathrooms"], sqft=r["sqft"],
            price=r["price"], price_max=r["price_max"], available=r["available"],
            listing_url=b.listing_url or units_url(cfg),
            apply_url=(contact_url(cfg, r) if r.get("contact_only")
                       else (apply_url(cfg, r["unit_id"], r["floorplan_id"]) if r.get("unit_id")
                             else f"{ole_base(cfg)}/guestlogin.aspx")),
            floorplan=r["floorplan"], source="securecafe", source_id=r.get("unit_id"),
            extra=({"contact_only": True, "phone": r.get("phone")} if r.get("contact_only") else {}),
        )
        units.append(u)
    return units


async def fetch(http: Any, b: Any, cfg: Optional[dict[str, Any]] = None) -> list[Unit]:
    cfg = dict(cfg or b.source)
    if not cfg.get("property_id"):
        cfg["property_id"] = await resolve_property_id(http, cfg)
        b.source["property_id"] = cfg["property_id"]  # cache for later polls
    html = await http.text(units_url(cfg))
    rows = parse_available_units(html)
    if not rows and "AvailUnitRow" not in html and "availableUnits" not in html and "No Apartments" not in html \
            and "no apartments" not in html.lower() and "currently unavailable" not in html.lower():
        # A totally unexpected page (challenge/login) -> raise so we don't mark everything as gone
        if "Just a moment" in html or "challenge" in html[:5000].lower():
            raise RuntimeError("securecafe returned a bot challenge page")
    return to_units(rows, cfg, b)
