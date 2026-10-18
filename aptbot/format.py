from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from .models import Unit


def _beds(u: Unit) -> str:
    if u.bedrooms is None:
        return "?bd"
    if u.bedrooms == 0:
        return "Studio"
    return f"{u.bedrooms}bd"


def _baths(u: Unit) -> Optional[str]:
    if u.bathrooms is None:
        return None
    b = u.bathrooms
    return f"{int(b)}ba" if float(b).is_integer() else f"{b}ba"


def _price(u: Unit) -> str:
    if not u.price:
        return "Price: call for details"
    s = f"${u.price:,.0f}"
    if u.price_max and u.price_max > u.price + 1:
        return f"from {s}/mo"
    return s + "/mo"


def _avail(u: Unit) -> str:
    """Earliest move-in: the property's availability date (today or earlier -> 'now')."""
    if not u.available:
        return "Earliest move-in: not published"
    if u.available == "Now":
        return "Earliest move-in: now"
    try:
        d = dt.date.fromisoformat(u.available)
        if d <= dt.date.today():
            return "Earliest move-in: now"
        return "Earliest move-in: " + d.strftime("%b %-d")
    except ValueError:
        return f"Earliest move-in: {u.available}"


def alert_text(u: Unit, relisted: bool = False, now_applyable: bool = False) -> str:
    tag = "NOW APPLYABLE" if now_applyable else ("RE-LISTED" if relisted else "NEW")
    kind = u.extra.get("building_label") or "height unknown"
    icon = "\U0001F3D9️" if u.extra.get("building_type") == "high-rise" else "\U0001F3E2"
    head = f"{icon} {tag} {_beds(u)} at {u.building_name} — {kind}"
    spec = [u.extra.get("display_unit") or f"Unit {u.unit_number}", _beds(u)]
    if _baths(u):
        spec[-1] = f"{_beds(u)}/{_baths(u)}"
    if u.sqft:
        spec.append(f"{u.sqft:,} sf")
    if u.floor:
        spec.append(f"Floor {u.floor}")
    line2 = " · ".join(spec)
    line3 = f"{_price(u)} · {_avail(u)}"
    lines = [head, line2, line3, f"Listing: {u.listing_url}"]
    if u.extra.get("contact_only"):
        phone = u.extra.get("phone")
        lines.append("Apply: not open online yet, the property has this unit as call-for-pricing."
                     + (f" Call {phone}." if phone else ""))
        if u.apply_url:
            lines.append(f"Contact form: {u.apply_url}")
    elif u.apply_url and u.apply_url != u.listing_url:
        lines.append(f"Apply: {u.apply_url}")
    elif u.apply_url:
        lines.append("Apply: same link (application is on the listing page)")
    return "\n".join(lines)


def digest_text(units: list[Unit], title: str) -> str:
    lines = [title]
    for u in units:
        lines.append(f"- {u.building_name} #{u.unit_number} {_beds(u)} {_price(u)} {_avail(u)}\n  {u.listing_url}")
    return "\n".join(lines)


def beds_label(bedrooms: list[int]) -> str:
    return "/".join("Studio" if b == 0 else f"{b}BR" for b in sorted(bedrooms))


def startup_text(n_buildings: int, n_matches: int, interval: int, bedrooms: list[int] | None = None) -> str:
    what = beds_label(bedrooms) if bedrooms else "matching"
    return (f"\U0001F3D9️ SF Apartment Bot is running on your Mac.\n"
            f"Watching {n_buildings} SF apartment buildings for {what} units, polling every ~{interval}s.\n"
            f"Baseline: {n_matches} matching units already listed (not alerted). "
            f"You'll get a text the moment a NEW one appears.")


def markdown_table(units: Iterable[Unit]) -> str:
    rows = ["| Building | Type | Unit | Beds/Baths | Sq ft | Floor | Price | Earliest move-in | Listing | Apply |", "|---|---|---|---|---|---|---|---|---|---|"]
    for u in sorted(units, key=lambda x: (x.building_name, x.unit_number)):
        rows.append(f"| {u.building_name} | {u.extra.get('building_label') or ''} | {u.unit_number} | {_beds(u)}/{_baths(u) or '?'} | {u.sqft or ''} | {u.floor or ''} | "
                    f"{_price(u)} | {u.available or ''} | [listing]({u.listing_url}) | {('[apply](' + u.apply_url + ')') if u.apply_url else ''} |")
    return "\n".join(rows)
