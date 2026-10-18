from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .config import Config, load_config
from .engine import Engine
from .filters import matches
from .format import alert_text, markdown_table
from .http import Http
from .notify import Notifier, build_channels
from .state import State


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logdir: Path = cfg.paths["logs"]
    logdir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(logdir / "aptbot.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def make_notifier(cfg: Config, override: Optional[list[str]] = None) -> Notifier:
    ncfg = dict(cfg.notify)
    if override:
        ncfg["channels"] = override
    return Notifier(build_channels(ncfg))


# ---------------------------------------------------------------- commands

async def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    state = State(cfg.paths["db"])
    notifier = make_notifier(cfg)
    if not notifier.channels:
        logging.error("no notification channels configured; refusing to run (set notify.channels in config.yaml)")
        return 2
    logging.info("aptbot %s starting; channels=%s; buildings=%d", __version__,
                 [c.name for c in notifier.channels], len(cfg.enabled_buildings))
    async with Http(per_host=int(cfg.poll.get("per_host", 2)), total=int(cfg.poll.get("concurrency", 6))) as http:
        eng = Engine(cfg, state, http, notifier, notify_existing=args.notify_existing)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: (eng.stop(), main_task.cancel() if main_task else None))
        try:
            await eng.run_forever(startup_message=not args.no_startup_message)
        except asyncio.CancelledError:
            logging.info("shutting down")
    return 0


async def cmd_once(args: argparse.Namespace, cfg: Config) -> int:
    state = State(cfg.paths["db"])
    notifier = make_notifier(cfg, ["console"] if not args.notify else None)
    buildings = cfg.enabled_buildings if not args.building else [cfg.building(b) for b in args.building]
    if args.building:
        for b in buildings:
            b.enabled = True
    async with Http() as http:
        eng = Engine(cfg, state, http, notifier, notify_existing=args.notify_existing)
        results = await asyncio.gather(*(eng.poll_building(b, notify=args.notify) for b in buildings))
        n = eng.write_current_matches()
    print("\n== Poll summary ==")
    for r in sorted(results, key=lambda x: x["building"]):
        if r.get("ok"):
            print(f"  OK   {r['building']:<24} {r['source']:<11} units={r['units']:<3} match={r['matches']:<3} new={r['new']:<3} gone={r['gone']}")
        else:
            print(f"  FAIL {r['building']:<24} {r.get('error')}")
    units = state.active_units()
    if not args.all_units:
        units = [u for u in units if u.building_id in {b.id for b in buildings} and matches(u, cfg.filters.merged(cfg.building(u.building_id).filters))]
    else:
        units = [u for u in units if u.building_id in {b.id for b in buildings}]
    print(f"\n== {'All' if args.all_units else 'Matching'} units currently listed ({len(units)}) ==")
    print(markdown_table(units))
    print(f"\n(current matches also written to {cfg.paths['current']}; {n} total across all buildings)")
    return 0


async def cmd_test_notify(args: argparse.Namespace, cfg: Config) -> int:
    notifier = make_notifier(cfg, args.channel)
    if not notifier.channels:
        print("no channels configured")
        return 2
    from .models import Unit
    if args.message:
        text = args.message
    else:
        u = Unit(building_id="test", building_name="Test Tower (this is a test)", unit_number="2405", bedrooms=2, bathrooms=2,
                 sqft=1102, floor="24", price=7250, available="Now", listing_url="https://example.com/listing",
                 apply_url="https://example.com/apply")
        text = alert_text(u)
    print("sending via", [c.name for c in notifier.channels], "...")
    res = await notifier.send(text)
    for k, v in res.items():
        print(f"  {k}: {'OK' if v is None else 'FAILED - ' + v}")
    return 0 if any(v is None for v in res.values()) else 1


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    p = cfg.paths["status"]
    if not p.exists():
        print("no status file yet (bot has not run)")
        return 1
    d = json.loads(p.read_text())
    print(f"updated: {d.get('updated')}  counts: {d.get('counts')}")
    for bid, st in sorted(d.get("buildings", {}).items()):
        flag = "OK  " if not st.get("error") else "ERR "
        print(f"  {flag} {bid:<24} last_ok={st.get('last_ok','-'):<19} units={st.get('units','-'):<3} match={st.get('matches','-'):<3} "
              f"src={st.get('source','-'):<10} {('error: ' + st['error'][:80]) if st.get('error') else ''}")
    state = State(cfg.paths["db"])
    rows = state.db.execute("SELECT ts, building_id, kind, key FROM events WHERE kind IN ('new','relisted','notify','gone') ORDER BY id DESC LIMIT 15").fetchall()
    if rows:
        print("\nrecent events:")
        for r in rows:
            print(f"  {r['ts']} {r['kind']:<9} {r['building_id']:<22} {r['key'] or ''}")
    return 0


def cmd_list(args: argparse.Namespace, cfg: Config) -> int:
    for b in cfg.buildings:
        flag = "on " if b.enabled else "off"
        fb = f" fallback={b.fallback['type']}" if b.fallback else ""
        en = " +apply-enrich" if b.apply_enrich else ""
        print(f"  [{flag}] {b.id:<24} {b.kind:<11}{fb}{en:<14} {b.name} ({b.address}, {b.stories or '?'} stories)")
    print(f"\n{len(cfg.enabled_buildings)} enabled / {len(cfg.buildings)} total; filters: {cfg.filters}")
    return 0


def cmd_reset(args: argparse.Namespace, cfg: Config) -> int:
    state = State(cfg.paths["db"])
    state.reset(args.building)
    print("state reset", f"for {args.building}" if args.building else "(all buildings)")
    return 0


async def cmd_resolve(args: argparse.Namespace, cfg: Config) -> int:
    from .sources import securecafe, sightmap
    async with Http() as http:
        for b in cfg.buildings:
            for src in (b.source, b.fallback, b.apply_enrich):
                if not src:
                    continue
                try:
                    if src.get("type", "securecafe") == "securecafe" and src.get("host") and not src.get("property_id"):
                        pid = await securecafe.resolve_property_id(http, src)
                        print(f"  {b.id}: securecafe {src['host']}/{src['slug']} -> property_id: {pid}")
                    elif src.get("type") == "sightmap":
                        api = await sightmap.get_api_url(http, src["embed"])
                        print(f"  {b.id}: sightmap {src['embed']} -> {api}")
                except Exception as e:  # noqa: BLE001
                    print(f"  {b.id}: FAILED {e}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="aptbot", description="SF high-rise apartment availability monitor")
    ap.add_argument("-c", "--config", help="path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="poll forever and send alerts")
    p.add_argument("--notify-existing", action="store_true", help="on first run, alert for units already listed")
    p.add_argument("--no-startup-message", action="store_true")
    p = sub.add_parser("once", help="poll every building once and print what is listed")
    p.add_argument("-b", "--building", action="append", help="building id (repeatable)")
    p.add_argument("--all-units", action="store_true", help="print every unit, not just filter matches")
    p.add_argument("--notify", action="store_true", help="actually send alerts for new units")
    p.add_argument("--notify-existing", action="store_true")
    p = sub.add_parser("test-notify", help="send a test alert")
    p.add_argument("--channel", action="append", help="override channel(s): imessage, twilio, ntfy, console")
    p.add_argument("-m", "--message")
    sub.add_parser("status", help="show last poll status per building")
    sub.add_parser("list", help="list configured buildings")
    p = sub.add_parser("reset", help="forget seen units (next run re-baselines)")
    p.add_argument("-b", "--building")
    sub.add_parser("resolve", help="resolve securecafe property ids / sightmap api urls")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, args.verbose)
    handler: Any = {
        "run": cmd_run, "once": cmd_once, "test-notify": cmd_test_notify, "status": cmd_status,
        "list": cmd_list, "reset": cmd_reset, "resolve": cmd_resolve,
    }[args.cmd]
    if asyncio.iscoroutinefunction(handler):
        return asyncio.run(handler(args, cfg))
    return handler(args, cfg)
