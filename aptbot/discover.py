"""Point this at an apartment building's website and it works out how to read its availability.

Detection is two-phase on purpose. First we fingerprint the page for every platform marker we know
(a single site often shows several, e.g. a SightMap widget AND a securecafe apply link). Then we
actually run each candidate source and keep the ones that return units, ranked by unit count, so
the recommendation is something that demonstrably works rather than a guess from a regex.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from .sources import get_source

log = logging.getLogger("aptbot.discover")

SECURECAFE = re.compile(r"https?://([a-z0-9-]+\.securecafe\.com)/onlineleasing/([a-z0-9-]+)/", re.I)
SIGHTMAP = re.compile(r"sightmap\.com/embed/([a-z0-9]{6,})", re.I)
ELISE = re.compile(r"https?://([a-z0-9-]+\.(?:com|ai))/building/([a-z0-9-]+)/units", re.I)
TITLE = re.compile(r"<title[^>]*>([^<]{2,120})</title>", re.I)
OG_TITLE = re.compile(r'<meta[^>]+property="og:site_name"[^>]+content="([^"]{2,80})"', re.I)
ADDRESS = re.compile(r'"streetAddress"\s*:\s*"([^"]{4,80})"', re.I)


@dataclass
class Candidate:
    type: str
    params: dict[str, Any]
    why: str
    units: Optional[int] = None
    error: Optional[str] = None
    sample: list[str] = field(default_factory=list)

    @property
    def works(self) -> bool:
        return self.units is not None and self.units > 0


class _Stub:
    """Minimal stand-in for a configured Building so a source can be run before it exists."""

    def __init__(self, url: str, name: str = "candidate"):
        self.id = "candidate"
        self.name = name
        self.listing_url = url
        self.apply_url = None
        self.source: dict[str, Any] = {}
        self.fallback = None
        self.apply_enrich = None
        self.sub_buildings = None
        self.raw_sub_buildings = None


GENERIC = re.compile(r"^(home|floor ?plans?|apartments?|availability|residences?|pricing|gallery|"
                     r"contact|amenities|photos|tours?|san francisco|apartments for rent)$", re.I)
JSONLD_NAME = re.compile(r'"@type"\s*:\s*"(?:ApartmentComplex|Residence|Apartment|LocalBusiness|Organization)"'
                         r'[^{}]{0,400}?"name"\s*:\s*"([^"]{2,60})"', re.I | re.S)
SITE_NAME = re.compile(r'<meta[^>]+(?:property|name)="(?:og:site_name|application-name)"[^>]+content="([^"]{2,60})"', re.I)


def site_name(html: str, url: str) -> str:
    """Building name, preferring explicit site metadata over the page title.

    Titles are usually marketing copy with the brand tacked on ("Studio, 1 & 2 Bedroom
    Apartments in SF | Chorus"), so when falling back to the title we take the shortest
    non-generic segment rather than the first.
    """
    for rx in (SITE_NAME, JSONLD_NAME):
        m = rx.search(html)
        if m:
            name = m.group(1).strip()
            if 2 < len(name) < 60 and not GENERIC.match(name):
                return name
    m = TITLE.search(html)
    if m:
        segments = [x.strip() for x in re.split(r"\s*[|·—–]\s*", m.group(1)) if x.strip()]
        usable = [x for x in segments if 2 < len(x) < 60 and not GENERIC.match(x)]
        if usable:
            return min(usable, key=len)
    return urlsplit(url).netloc.replace("www.", "").split(".")[0].replace("-", " ").title()


def street_address(html: str) -> Optional[str]:
    m = ADDRESS.search(html)
    return m.group(1).strip() if m else None


def slug_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_+", "_", s)[:40] or "building"


def fingerprint(html: str, url: str) -> list[Candidate]:
    """Every platform marker present on the page, most specific first."""
    out: list[Candidate] = []
    host = urlsplit(url).netloc.lower()

    if "ysi.unitsList" in html:
        out.append(Candidate("rentcafe_site", {"urls": [url]}, "page embeds ysi.unitsList"))
    if 'id="jd-fp-data-script-app"' in html:
        origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/"
        out.append(Candidate("jdfp", {"url": url, "base_url": origin}, "page embeds the jd-fp floor plan app"))
    for m in SIGHTMAP.finditer(html):
        embed = m.group(1)
        if embed.lower() in ("api", "embed"):
            continue
        out.append(Candidate("sightmap", {"embed": embed}, f"SightMap embed {embed}"))
        break
    m = SECURECAFE.search(html)
    if m:
        out.append(Candidate("securecafe", {"host": m.group(1).lower(), "slug": m.group(2)},
                             f"links to securecafe online leasing ({m.group(2)})"))
    if "ea5.unitAvailability" in html:
        out.append(Candidate("equity", {"url": url}, "page embeds ea5.unitAvailability"))
    if "window.udr.jsonObjPropertyViewModel" in html or "udr.com" in host:
        out.append(Candidate("udr", {"url": url}, "page embeds the UDR property view-model"))
    if '"units":[{"unitId"' in html or "avaloncommunities.com" in host:
        out.append(Candidate("avalon", {"url": url}, "page embeds the AvalonBay units array"))
    if "greystar.com" in host:
        out.append(Candidate("greystar", {"url": url}, "Greystar property page"))
    if "relatedrentals.com" in host:
        out.append(Candidate("related", {"url": url}, "Related Rentals property page"))
    if '"propertyUnits"' in html or "data-units=" in html or "entrata.com" in html:
        out.append(Candidate("entrata", {"url": url}, "Entrata unit JSON on the page"))
    m = ELISE.search(html)
    if m:
        out.append(Candidate("elise", {"host": m.group(1), "slug": m.group(2)}, "links to an EliseAI application portal"))
    return out


async def enrich_params(http: Any, c: Candidate) -> None:
    """Resolve the ids a source needs before it can run."""
    if c.type == "securecafe" and not c.params.get("property_id"):
        from .sources import securecafe
        try:
            c.params["property_id"] = await securecafe.resolve_property_id(http, c.params)
        except Exception as e:  # noqa: BLE001
            c.error = f"could not resolve property id: {e}"


async def validate(http: Any, c: Candidate, name: str, url: str) -> None:
    """Actually run the source. A candidate only counts if it returns units."""
    if c.error:
        return
    stub = _Stub(url, name)
    try:
        units = await get_source(c.type).fetch(http, stub, c.params)
    except Exception as e:  # noqa: BLE001
        c.error = f"{type(e).__name__}: {e}"
        return
    c.units = len(units)
    c.sample = [u.short() for u in units[:3]]


async def discover(http: Any, url: str) -> dict[str, Any]:
    html = await http.text(url)
    name = site_name(html, url)
    cands = fingerprint(html, url)
    for c in cands:
        await enrich_params(http, c)
        await validate(http, c, name, url)
    working = sorted([c for c in cands if c.works], key=lambda c: -(c.units or 0))
    return {"url": url, "name": name, "address": street_address(html),
            "candidates": cands, "working": working}


def render_block(building_id: str, name: str, primary: Candidate, *, address: str = "",
                 stories: Optional[int] = None, listing_url: str = "",
                 fallback: Optional[Candidate] = None, enrich: Optional[Candidate] = None) -> str:
    def inline(params: dict[str, Any], type_: str) -> str:
        parts = [f"type: {type_}"]
        for k, v in params.items():
            if isinstance(v, list):
                parts.append(f"{k}: [{', '.join(repr(str(x)) for x in v)}]".replace("'", '"'))
            elif isinstance(v, str) and (v.startswith("http") or "-" in v or "." in v):
                parts.append(f'{k}: "{v}"')
            else:
                parts.append(f"{k}: {v}")
        return "{" + ", ".join(parts) + "}"

    lines = [f"  {building_id}:", f"    name: {name}"]
    if address:
        lines.append(f"    address: {address}")
    if stories is not None:
        lines.append(f"    stories: {stories}")
    if listing_url:
        lines.append(f"    listing_url: {listing_url}")
    lines.append(f"    source: {inline(primary.params, primary.type)}")
    if fallback is not None:
        lines.append(f"    fallback: {inline(fallback.params, fallback.type)}")
    if enrich is not None:
        lines.append(f"    apply_enrich: {inline(enrich.params, 'securecafe')}".replace("type: securecafe, ", ""))
    return "\n".join(lines) + "\n"
