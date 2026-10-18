"""Source registry. Each source module exposes `async def fetch(http, b: Building) -> list[Unit]`."""
from __future__ import annotations

from importlib import import_module
from typing import Any

SOURCE_MODULES = {
    "securecafe": "aptbot.sources.securecafe",
    "sightmap": "aptbot.sources.sightmap",
    "jdfp": "aptbot.sources.jdfp",
    "greystar": "aptbot.sources.greystar",
    "equity": "aptbot.sources.equity",
    "related": "aptbot.sources.related",
    "avalon": "aptbot.sources.avalon",
    "udr": "aptbot.sources.udr",
    "nema": "aptbot.sources.nema",
    "rentcafe_site": "aptbot.sources.rentcafe_site",
    "elise": "aptbot.sources.elise",
    "entrata": "aptbot.sources.entrata",
}


def get_source(kind: str) -> Any:
    if kind not in SOURCE_MODULES:
        raise KeyError(f"unknown source type {kind!r}; known: {sorted(SOURCE_MODULES)}")
    return import_module(SOURCE_MODULES[kind])
