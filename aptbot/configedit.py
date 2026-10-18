"""Safe, comment-preserving edits to config.yaml.

The config is hand-written and heavily commented, so we never round-trip it through a YAML
dumper (that would strip every comment). Instead we make surgical line edits and then verify the
result still parses and still has the expected shape, restoring a backup if it does not.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import yaml


class ConfigEditError(RuntimeError):
    pass


def _verify(text: str, expect_buildings: Optional[int] = None) -> dict[str, Any]:
    try:
        d = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigEditError(f"edit produced invalid YAML: {e}") from e
    if not isinstance(d.get("buildings"), dict) or not d["buildings"]:
        raise ConfigEditError("edit lost the buildings section")
    if expect_buildings is not None and len(d["buildings"]) != expect_buildings:
        raise ConfigEditError(f"expected {expect_buildings} buildings, got {len(d['buildings'])}")
    return d


def edit(path: Path, fn: Callable[[str], str], expect_buildings: Optional[int] = None) -> dict[str, Any]:
    """Apply fn to the config text, verify, and write atomically with a .bak."""
    original = path.read_text()
    before = len((yaml.safe_load(original) or {}).get("buildings") or {})
    updated = fn(original)
    if updated == original:
        raise ConfigEditError("nothing changed")
    d = _verify(updated, expect_buildings if expect_buildings is not None else before)
    shutil.copy2(path, path.with_suffix(".yaml.bak"))
    path.write_text(updated)
    return d


# ---------------------------------------------------------------- recipients

def _render_list(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def get_recipients(text: str) -> list[str]:
    d = yaml.safe_load(text) or {}
    to = ((d.get("notify") or {}).get("to")) or []
    if isinstance(to, str):
        return [x.strip() for x in to.split(",") if x.strip()]
    return [str(x) for x in to]


def set_recipients(text: str, values: list[str]) -> str:
    if not values:
        raise ConfigEditError("refusing to leave zero recipients; the bot would have nobody to text")
    m = re.search(r"^([ \t]*to:)[ \t]*(.*?)([ \t]*#.*)?$", text, re.M)
    if not m:
        raise ConfigEditError("could not find the notify.to line")
    comment = m.group(3) or "   # one text per recipient"
    return text[:m.start()] + f"{m.group(1)} {_render_list(values)}{comment}" + text[m.end():]


# ---------------------------------------------------------------- filters

SCALAR_FILTERS = {"max_price": float, "min_price": float, "min_sqft": int, "available_by": str,
                  "include_unknown_bedrooms": bool}


def set_filter(text: str, key: str, value: Any) -> str:
    """Set (or clear, when value is None) one key inside the top-level `filters:` block."""
    block = re.search(r"^filters:[ \t]*$", text, re.M)
    if not block:
        raise ConfigEditError("no filters: section")
    start = block.end()
    end_m = re.search(r"^\S", text[start:], re.M)
    end = start + (end_m.start() if end_m else len(text) - start)
    body = text[start:end]

    if key == "bedrooms":
        rendered = "[" + ", ".join(str(int(v)) for v in value) + "]"
        line = f"  bedrooms: {rendered}"
    elif value is None:
        body_new = re.sub(rf"^[ \t]*{re.escape(key)}:.*$\n?", "", body, flags=re.M)
        return text[:start] + body_new + text[end:]
    elif isinstance(value, bool):
        line = f"  {key}: {'true' if value else 'false'}"
    elif isinstance(value, float) and value.is_integer():
        line = f"  {key}: {int(value)}"
    else:
        line = f"  {key}: {value}"

    existing = re.search(rf"^[ \t]*{re.escape(key)}:.*$", body, re.M)
    if existing:
        keep_comment = re.search(r"([ \t]+#.*)$", existing.group(0))
        body_new = body[:existing.start()] + line + (keep_comment.group(1) if keep_comment else "") + body[existing.end():]
    else:
        body_new = body.rstrip("\n") + "\n" + line + "\n"
    return text[:start] + body_new + text[end:]


# ---------------------------------------------------------------- buildings

def building_ids(text: str) -> list[str]:
    d = yaml.safe_load(text) or {}
    return list((d.get("buildings") or {}).keys())


def set_enabled(text: str, building_id: str, enabled: bool) -> str:
    m = re.search(rf"^  {re.escape(building_id)}:[ \t]*$", text, re.M)
    if not m:
        raise ConfigEditError(f"no building {building_id!r} in config")
    start = m.end()
    nxt = re.search(r"^  \S", text[start:], re.M)
    end = start + (nxt.start() if nxt else len(text) - start)
    body = text[start:end]
    line = f"    enabled: {'true' if enabled else 'false'}"
    existing = re.search(r"^[ \t]*enabled:.*$", body, re.M)
    body_new = (body[:existing.start()] + line + body[existing.end():]) if existing else ("\n" + line + body)
    return text[:start] + body_new + text[end:]


def append_building(text: str, building_id: str, block: str) -> str:
    if re.search(rf"^  {re.escape(building_id)}:[ \t]*$", text, re.M):
        raise ConfigEditError(f"building id {building_id!r} already exists")
    if not re.search(r"^buildings:\s*$", text, re.M):
        raise ConfigEditError("no buildings: section")
    tail = text.rstrip("\n")
    # buildings is the last top-level section in this file; confirm before appending to the end
    last_top = max((m.start() for m in re.finditer(r"^\S", tail, re.M)), default=-1)
    if not tail[last_top:].startswith("buildings:"):
        raise ConfigEditError("buildings: is not the last top-level section; append manually")
    return tail + "\n" + block.rstrip("\n") + "\n"
