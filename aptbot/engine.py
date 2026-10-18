"""Polling engine: fetch -> reconcile with state -> filter -> enrich -> notify."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import random
import time
import traceback
from typing import Any, Optional

from .config import Building, Config
from .enrich import enrich_apply_links
from .filters import matches
from .format import alert_text, beds_label, markdown_table, startup_text
from .http import Http
from .models import Unit
from .notify import Notifier
from .sources import get_source
from .sources.util import building_label
from .state import State

log = logging.getLogger("aptbot.engine")


class Engine:
    def __init__(self, cfg: Config, state: State, http: Http, notifier: Notifier, notify_existing: bool = False):
        self.cfg, self.state, self.http, self.notifier = cfg, state, http, notifier
        self.notify_existing = notify_existing
        self.status: dict[str, dict[str, Any]] = {}
        try:
            self.status = json.loads(cfg.paths["status"].read_text()).get("buildings", {}) or {}
        except Exception:  # noqa: BLE001
            pass
        self._status_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ fetch
    async def fetch_units(self, b: Building) -> tuple[list[Unit], str]:
        """Fetch from the primary source; on failure try the fallback source. Returns (units, source_used)."""
        errors = []
        for label, src in (("primary", b.source), ("fallback", b.fallback)):
            if not src:
                continue
            try:
                mod = get_source(src["type"])
                units = await mod.fetch(self.http, b, src)
                for u in units:
                    if u.price is not None and u.price < 300:  # placeholder like -1 / 1 -> unknown
                        u.price, u.price_max = None, None
                    if u.price_max is not None and u.price_max < 300:
                        u.price_max = None
                    u.extra["building_type"] = b.classification
                    u.extra["building_label"] = b.classification_label
                    if b.raw_sub_buildings:
                        u.building_name = building_label(b.name, u.floorplan, b.raw_sub_buildings)
                if label == "fallback":
                    log.warning("[%s] primary source failed (%s); used fallback %s", b.id, errors[-1], src["type"])
                return units, src["type"]
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src['type']}: {type(e).__name__}: {e}")
                log.debug("[%s] %s source error\n%s", b.id, label, traceback.format_exc())
        raise RuntimeError("; ".join(errors) or "no source configured")

    # ------------------------------------------------------------------ poll
    async def poll_building(self, b: Building, notify: bool = True) -> dict[str, Any]:
        t0 = time.monotonic()
        summary: dict[str, Any] = {"building": b.id, "ok": False}
        try:
            units, used = await self.fetch_units(b)
        except Exception as e:  # noqa: BLE001
            summary["error"] = str(e)
            log.error("[%s] fetch failed: %s", b.id, e)
            await self._set_status(b.id, ok=False, error=str(e))
            return summary
        filt = self.cfg.filters.merged(b.filters)
        baseline = not self.state.building_seen_before(b.id)
        was_contact_only = {u.key for u in self.state.active_units(b.id) if u.extra.get("contact_only")}
        res = self.state.reconcile(b.id, units, relist_days=int(self.cfg.poll.get("relist_days", 21)))
        opened = [u for u in res["current"]
                  if u.key in was_contact_only and not u.extra.get("contact_only") and matches(u, filt)]
        fresh = [u for u in res["new"] + res["relisted"] if matches(u, filt)]
        current_matches = [u for u in res["current"] if matches(u, filt)]
        self.state.mark_matched([u.key for u in current_matches])
        if baseline:
            self.state.mark_baseline(b.id)
            self.state.event("baseline", b.id, detail={"units": len(units), "matches": len(current_matches)})
            log.info("[%s] baseline via %s: %d units, %d match filters%s", b.id, used, len(units), len(current_matches),
                     "" if self.notify_existing else " (not alerting existing)")
            if not self.notify_existing:
                fresh = []
        else:
            log.info("[%s] via %s: %d units (%d match) | new=%d relisted=%d gone=%d | %.1fs", b.id, used, len(units),
                     len(current_matches), len(res["new"]), len(res["relisted"]), len(res["gone"]), time.monotonic() - t0)
        for u in res["gone"]:
            self.state.event("gone", b.id, u.key, detail=u.short())
        for u in opened:  # application just opened on a unit we already told you about
            text = alert_text(u, now_applyable=True)
            self.state.event("now_applyable", b.id, u.key, detail=u.to_dict())
            if notify:
                results = await self.notifier.send(text)
                self.state.event("notify", b.id, u.key, detail=results)
                log.info("[%s] NOW APPLYABLE %s -> %s", b.id, u.short(), results)
            else:
                print(text, flush=True)
        sent = 0
        if fresh:
            try:
                n = await enrich_apply_links(self.http, b, fresh)
                if n:
                    log.info("[%s] attached %d unit-specific apply links", b.id, n)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] enrichment error: %s", b.id, e)
            for u in fresh:
                if self.state.was_notified(u.key):
                    continue
                relisted = u in res["relisted"]
                text = alert_text(u, relisted=relisted)
                self.state.event("new" if not relisted else "relisted", b.id, u.key, detail=u.to_dict())
                if notify:
                    results = await self.notifier.send(text)
                    ok = any(v is None for v in results.values()) if results else False
                    self.state.event("notify", b.id, u.key, detail=results)
                    if ok or not results:
                        self.state.mark_notified([u.key])
                        sent += 1
                    log.info("[%s] ALERT %s -> %s", b.id, u.short(), results)
                else:
                    print(text, flush=True)
        summary.update(ok=True, source=used, units=len(units), matches=len(current_matches), new=len(res["new"]),
                       relisted=len(res["relisted"]), gone=len(res["gone"]), alerted=sent + len(opened),
                       now_applyable=len(opened), baseline=baseline)
        await self._set_status(b.id, ok=True, **{k: v for k, v in summary.items() if k not in ("building", "ok")})
        return summary

    # ------------------------------------------------------------------ status files
    async def _set_status(self, bid: str, ok: bool, **kw: Any) -> None:
        async with self._status_lock:
            st = self.status.setdefault(bid, {})
            now = dt.datetime.now().replace(microsecond=0).isoformat()
            st.update(kw)
            if ok:
                st["last_ok"] = now
                st.pop("error", None)
            else:
                st["last_error"] = now
            self._write_status()

    def _write_status(self) -> None:
        try:
            p = self.cfg.paths["status"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"updated": dt.datetime.now().isoformat(timespec="seconds"),
                                     "counts": self.state.counts(), "buildings": self.status}, indent=1))
        except Exception as e:  # noqa: BLE001
            log.debug("status write failed: %s", e)

    def write_current_matches(self) -> int:
        units = [u for u in self.state.active_units() if matches(u, self.cfg.filters.merged(self.cfg.building(u.building_id).filters))]
        p = self.cfg.paths["current"]
        try:
            p.write_text(f"# Current {beds_label(self.cfg.filters.bedrooms)} availability ({dt.datetime.now():%Y-%m-%d %H:%M})\n\n{len(units)} units\n\n" + markdown_table(units) + "\n")
        except Exception as e:  # noqa: BLE001
            log.debug("current_matches write failed: %s", e)
        return len(units)

    # ------------------------------------------------------------------ loops
    def interval_for(self, b: Building) -> int:
        if b.interval:
            return int(b.interval)
        if b.kind == "securecafe":
            return int(self.cfg.poll.get("securecafe_interval", 120))
        return int(self.cfg.poll.get("interval", 60))

    async def building_loop(self, b: Building) -> None:
        base = self.interval_for(b)
        jitter = float(self.cfg.poll.get("jitter", 0.15))
        backoff = 0
        await asyncio.sleep(random.uniform(0, min(20, base * 0.5)))  # stagger start
        while not self._stop.is_set():
            res = await self.poll_building(b)
            if res.get("ok"):
                backoff = 0
                delay = base * random.uniform(1 - jitter, 1 + jitter)
            else:
                backoff = min(backoff + 1, 5)
                delay = min(base * (2 ** backoff), 900) * random.uniform(0.8, 1.2)
                log.warning("[%s] backing off %.0fs after failure", b.id, delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def run_forever(self, startup_message: bool = True) -> None:
        buildings = self.cfg.enabled_buildings
        log.info("starting %d building loops", len(buildings))
        first_run = any(not self.state.building_seen_before(b.id) for b in buildings)
        # Do one synchronous sweep first so the startup message has real numbers.
        results = await asyncio.gather(*(self.poll_building(b) for b in buildings))
        n_matches = self.write_current_matches()
        ok = sum(1 for r in results if r.get("ok"))
        log.info("initial sweep: %d/%d buildings ok, %d matching units currently listed", ok, len(buildings), n_matches)
        if startup_message and first_run:
            await self.notifier.send(startup_text(len(buildings), n_matches, int(self.cfg.poll.get("interval", 60)), self.cfg.filters.bedrooms))
        tasks = [asyncio.create_task(self.building_loop(b), name=f"loop:{b.id}") for b in buildings]
        tasks.append(asyncio.create_task(self._housekeeping(), name="housekeeping"))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._stop.set()
            raise

    async def _housekeeping(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=300)
            except asyncio.TimeoutError:
                self.write_current_matches()

    def stop(self) -> None:
        self._stop.set()
