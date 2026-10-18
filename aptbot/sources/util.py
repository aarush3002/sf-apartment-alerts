from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Optional

_MONEY = re.compile(r"\$?\s*([0-9][0-9,]*)(?:\.(\d+))?")


def parse_money(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = _MONEY.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
    except ValueError:
        return None


def parse_money_range(s: Any) -> tuple[Optional[float], Optional[float]]:
    if s is None:
        return None, None
    if isinstance(s, (int, float)):
        return float(s), float(s)
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", str(s))]
    if not nums:
        v = parse_money(s)
        return v, v
    return min(nums), max(nums)


def parse_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, bool):
        return int(s)
    if isinstance(s, (int, float)):
        return int(s)
    m = re.search(r"\d[\d,]*", str(s))
    return int(m.group(0).replace(",", "")) if m else None


def parse_beds(s: Any) -> Optional[int]:
    """'Studio'->0, '2 Bed'->2, '2'->2, 'Jr. 1 Bed'->1, 0.5 -> 0."""
    if s is None:
        return None
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return int(s) if float(s) >= 1 else 0
    t = str(s).strip().lower()
    if not t:
        return None
    if "studio" in t or "efficiency" in t:
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    v = float(m.group(1))
    return int(v) if v >= 1 else 0


def parse_baths(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    return float(m.group(1)) if m else None


def fmt_date(value: Any) -> Optional[str]:
    """Normalize many date shapes to 'YYYY-MM-DD' or 'Now'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    low = s.lower()
    if low in ("now", "available", "available now", "immediate", "immediately", "today") or ("now" in low and len(low) < 20):
        return "Now"
    m = re.match(r"/Date\((\d+)", s)
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    if re.fullmatch(r"\d{9,11}", s):
        return fmt_date(int(s))
    md = re.fullmatch(r"(\d{1,2})/(\d{1,2})", s)
    if md:  # "09/09" style (Related): assume the next occurrence of that month/day
        today = dt.date.today()
        try:
            d = dt.date(today.year, int(md.group(1)), int(md.group(2)))
            if d < today - dt.timedelta(days=30):
                d = d.replace(year=today.year + 1)
            return d.isoformat()
        except ValueError:
            return s
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def date_is_now_or_past(iso: Optional[str]) -> bool:
    if not iso or iso == "Now":
        return True
    try:
        return dt.date.fromisoformat(iso) <= dt.date.today()
    except ValueError:
        return False


def extract_json_after(text: str, marker: str, open_ch: str = "{", close_ch: str = "}") -> Any:
    """Find `marker` in text, then bracket-match the JSON value that follows it."""
    i = text.find(marker)
    if i < 0:
        raise ValueError(f"marker {marker!r} not found")
    j = text.find(open_ch, i + (marker.find(open_ch) if open_ch in marker else len(marker)))
    if j < 0:
        raise ValueError("no JSON start after marker")
    return extract_json_at(text, j, open_ch, close_ch)


def extract_json_at(text: str, j: int, open_ch: str = "{", close_ch: str = "}") -> Any:
    """Bracket-match and parse the JSON value that starts exactly at index j."""
    depth = 0
    in_str = False
    esc = False
    for k in range(j, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return json.loads(text[j:k + 1])
    raise ValueError("unbalanced JSON")


def building_label(default: str, floorplan: Optional[str], sub_buildings: Optional[dict[str, str]]) -> str:
    """Some properties (e.g. Mission Rock = Canyon + Verde) expose several towers under one feed and
    prefix the floor plan name with the tower; map that prefix to a display name."""
    if not sub_buildings or not floorplan:
        return default
    fp = floorplan.strip().lower()
    for prefix, label in sub_buildings.items():
        if fp.startswith(prefix.lower()):
            return label
    return default


def decode_next_flight(html: str) -> str:
    """Concatenate Next.js RSC flight payload chunks (self.__next_f.push([1,"..."])) into one string."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    out = []
    for c in chunks:
        try:
            out.append(json.loads('"' + c + '"'))
        except json.JSONDecodeError:
            out.append(c)
    return "".join(out)
