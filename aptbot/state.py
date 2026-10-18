"""SQLite store of every unit we've observed, to decide what is *new*."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Unit

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
  key TEXT PRIMARY KEY,
  building_id TEXT NOT NULL,
  unit_number TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  gone_at TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  notified_at TEXT,
  matched INTEGER NOT NULL DEFAULT 0,
  price REAL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_building ON units(building_id, active);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, building_id TEXT, key TEXT, kind TEXT NOT NULL, detail TEXT
);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class State:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ---- meta -------------------------------------------------------------
    def get_meta(self, k: str, default: Optional[str] = None) -> Optional[str]:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def building_seen_before(self, bid: str) -> bool:
        return self.get_meta(f"baseline:{bid}") is not None

    def mark_baseline(self, bid: str) -> None:
        self.set_meta(f"baseline:{bid}", now_iso())

    # ---- events -----------------------------------------------------------
    def event(self, kind: str, building_id: Optional[str] = None, key: Optional[str] = None, detail: Any = None) -> None:
        self.db.execute("INSERT INTO events(ts,building_id,key,kind,detail) VALUES(?,?,?,?,?)",
                        (now_iso(), building_id, key, kind, json.dumps(detail) if detail is not None else None))

    # ---- units ------------------------------------------------------------
    def reconcile(self, bid: str, units: Iterable[Unit], relist_days: int = 21) -> dict[str, list[Unit]]:
        """Upsert the current snapshot for one building.

        Returns {"new": [...], "relisted": [...], "gone": [...], "current": [...]}.
        A unit is *new* if its key was never seen; *relisted* if it was seen before, disappeared,
        and has been gone for more than `relist_days` days.
        """
        ts = now_iso()
        units = list(units)
        seen_keys = set()
        new, relisted, current = [], [], []
        with self.db:  # single transaction
            self.db.execute("BEGIN")
            for u in units:
                k = u.key
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                current.append(u)
                row = self.db.execute("SELECT * FROM units WHERE key=?", (k,)).fetchone()
                data = json.dumps(u.to_dict(), default=str)
                if row is None:
                    self.db.execute(
                        "INSERT INTO units(key,building_id,unit_number,first_seen,last_seen,active,price,data) VALUES(?,?,?,?,?,1,?,?)",
                        (k, bid, u.unit_number, ts, ts, u.price, data))
                    new.append(u)
                else:
                    is_relist = False
                    if not row["active"] and row["gone_at"]:
                        gone = dt.datetime.fromisoformat(row["gone_at"])
                        if dt.datetime.now(dt.timezone.utc) - gone > dt.timedelta(days=relist_days):
                            is_relist = True
                    self.db.execute(
                        "UPDATE units SET last_seen=?, active=1, gone_at=NULL, price=?, data=?, unit_number=?"
                        + (", first_seen=?, notified_at=NULL" if is_relist else "") + " WHERE key=?",
                        ((ts, u.price, data, u.unit_number) + ((ts,) if is_relist else ()) + (k,)))
                    if is_relist:
                        relisted.append(u)
            gone_rows = self.db.execute("SELECT key, data FROM units WHERE building_id=? AND active=1", (bid,)).fetchall()
            gone = []
            for r in gone_rows:
                if r["key"] not in seen_keys:
                    self.db.execute("UPDATE units SET active=0, gone_at=? WHERE key=?", (ts, r["key"]))
                    try:
                        d = json.loads(r["data"])
                        d.pop("key", None)
                        gone.append(Unit(**d))
                    except Exception:  # noqa: BLE001
                        pass
            self.db.execute("COMMIT")
        return {"new": new, "relisted": relisted, "gone": gone, "current": current}

    def mark_notified(self, keys: Iterable[str]) -> None:
        ts = now_iso()
        self.db.executemany("UPDATE units SET notified_at=?, matched=1 WHERE key=?", [(ts, k) for k in keys])

    def mark_matched(self, keys: Iterable[str]) -> None:
        self.db.executemany("UPDATE units SET matched=1 WHERE key=?", [(k,) for k in keys])

    def was_notified(self, key: str) -> bool:
        r = self.db.execute("SELECT notified_at FROM units WHERE key=?", (key,)).fetchone()
        return bool(r and r["notified_at"])

    def active_units(self, bid: Optional[str] = None) -> list[Unit]:
        q = "SELECT data FROM units WHERE active=1" + (" AND building_id=?" if bid else "") + " ORDER BY building_id, unit_number"
        rows = self.db.execute(q, (bid,) if bid else ()).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d.pop("key", None)
            out.append(Unit(**d))
        return out

    def counts(self) -> dict[str, int]:
        r = self.db.execute("SELECT COUNT(*) c, SUM(active) a, SUM(notified_at IS NOT NULL) n FROM units").fetchone()
        return {"total": r["c"] or 0, "active": r["a"] or 0, "notified": r["n"] or 0}

    def reset(self, bid: Optional[str] = None) -> None:
        if bid:
            self.db.execute("DELETE FROM units WHERE building_id=?", (bid,))
            self.db.execute("DELETE FROM meta WHERE k=?", (f"baseline:{bid}",))
        else:
            self.db.execute("DELETE FROM units")
            self.db.execute("DELETE FROM meta")
            self.db.execute("DELETE FROM events")

    def close(self) -> None:
        self.db.close()
