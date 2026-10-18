from __future__ import annotations

import datetime as dt

from .config import Filters
from .models import Unit


def matches(u: Unit, f: Filters) -> bool:
    if u.bedrooms is None:
        if not f.include_unknown_bedrooms:
            return False
    elif u.bedrooms not in f.bedrooms:
        return False
    if f.max_price is not None and u.price is not None and u.price > f.max_price:
        return False
    if f.min_price is not None and u.price is not None and u.price < f.min_price:
        return False
    if f.min_sqft is not None and u.sqft is not None and u.sqft < f.min_sqft:
        return False
    if f.available_by and u.available and u.available != "Now":
        try:
            if dt.date.fromisoformat(u.available) > dt.date.fromisoformat(f.available_by):
                return False
        except ValueError:
            pass
    return True
