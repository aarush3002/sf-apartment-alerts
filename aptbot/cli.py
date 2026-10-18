from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from . import configedit as ce
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




# ---------------------------------------------------------------- config editing

def _config_path(args: argparse.Namespace) -> Path:
    import os
    return Path(args.config or os.environ.get("APTBOT_CONFIG") or (Path(__file__).resolve().parent.parent / "config.yaml"))


def _maybe_restart(args: argparse.Namespace) -> None:
    label = "com.aptbot.sf"
    if getattr(args, "restart", False):
        import subprocess
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"], check=False)
        print("  restarted the running bot")
    else:
        print(f"  restart to apply: launchctl kickstart -k gui/$(id -u)/{label}")


def cmd_filters(args: argparse.Namespace, cfg: Config) -> int:
    path = _config_path(args)
    changes: list[tuple[str, Any]] = []
    if args.bedrooms is not None:
        try:
            beds = sorted({int(x) for x in re.split(r"[,\s]+", args.bedrooms.strip()) if x != ""})
        except ValueError:
            print(f"could not read bedrooms from {args.bedrooms!r}; use something like 0,1,2")
            return 2
        changes.append(("bedrooms", beds))
    for key, val in (("max_price", args.max_price), ("min_price", args.min_price),
                     ("min_sqft", args.min_sqft), ("available_by", args.available_by)):
        if val is not None:
            changes.append((key, None if str(val).lower() in ("none", "clear", "") else val))
    if args.clear:
        for key in args.clear:
            changes.append((key, None))
    if not changes:
        f = cfg.filters
        print("current filters:")
        print(f"  bedrooms:      {f.bedrooms}   (0 = studio)")
        for k in ("min_price", "max_price", "min_sqft", "available_by"):
            print(f"  {k+':':<15}{getattr(f, k) if getattr(f, k) is not None else '(any)'}")
        print(f"  include_unknown_bedrooms: {f.include_unknown_bedrooms}")
        print("\nexamples:")
        print("  aptbot filters --bedrooms 0,1,2 --max-price 6000")
        print("  aptbot filters --clear max_price")
        return 0
    def apply(text: str) -> str:
        for key, val in changes:
            text = ce.set_filter(text, key, val)
        return text
    try:
        d = ce.edit(path, apply)
    except ce.ConfigEditError as e:
        print(f"no change: {e}")
        return 1
    print("filters now:", d.get("filters"))
    _maybe_restart(args)
    return 0


def cmd_recipients(args: argparse.Namespace, cfg: Config) -> int:
    from .notify.imessage import normalize_number
    path = _config_path(args)
    current = ce.get_recipients(path.read_text())
    if args.action in (None, "list"):
        print("texting:" if current else "no recipients configured")
        for r in current:
            print("  ", r)
        print("\n  aptbot recipients add +14155551234 | remove +14155551234")
        return 0
    wanted = [normalize_number(n) for n in (args.numbers or [])]
    if not wanted:
        print("give at least one phone number")
        return 2
    if args.action == "add":
        new = current + [n for n in wanted if n not in current]
    elif args.action == "remove":
        new = [n for n in current if n not in wanted]
    else:  # set
        new = wanted
    if new == current:
        print("already the case; nothing to do")
        return 0
    try:
        ce.edit(path, lambda t: ce.set_recipients(t, new))
    except ce.ConfigEditError as e:
        print(f"no change: {e}")
        return 1
    print("texting:", ", ".join(new))
    _maybe_restart(args)
    return 0


def cmd_toggle(args: argparse.Namespace, cfg: Config) -> int:
    path = _config_path(args)
    want = args.cmd == "enable"
    try:
        ce.edit(path, lambda t: ce.set_enabled(t, args.building, want))
    except ce.ConfigEditError as e:
        print(f"no change: {e}")
        return 1
    print(f"{args.building} is now {'enabled' if want else 'disabled'}")
    _maybe_restart(args)
    return 0


async def cmd_add_building(args: argparse.Namespace, cfg: Config) -> int:
    from .discover import discover, render_block, slug_id
    path = _config_path(args)
    print(f"inspecting {args.url} ...")
    async with Http() as http:
        try:
            res = await discover(http, args.url)
        except Exception as e:  # noqa: BLE001
            print(f"could not read that page: {type(e).__name__}: {e}")
            return 1
    name = args.name or res["name"]
    print(f"\nlooks like: {name}")
    if res["address"]:
        print(f"address:    {res['address']}")
    if not res["candidates"]:
        print("\nNo known leasing platform found on that page.")
        print("Open the page source and look for one of: securecafe onlineleasing links, a")
        print("sightmap.com/embed hash, jd-fp-data-script-app, ysi.unitsList, ea5.unitAvailability,")
        print("window.udr.jsonObjPropertyViewModel, or an Entrata propertyUnits blob.")
        return 1
    print("\nplatforms detected:")
    for c in res["candidates"]:
        status = f"{c.units} units" if c.works else (c.error or "no units")
        print(f"  {'OK ' if c.works else '   '} {c.type:<14} {status:<44} ({c.why})")
        for line in c.sample:
            print(f"        {line}")
    working = res["working"]
    if not working:
        print("\nNone of them returned units. The building may simply be full right now, or the")
        print("site changed. Re-run later, or pass --force to add it with the best guess anyway.")
        if not args.force:
            return 1
        working = res["candidates"][:1]
    primary = working[0]
    fallback = working[1] if len(working) > 1 else None
    enrich = next((c for c in res["candidates"]
                   if c.type == "securecafe" and c is not primary and c.params.get("property_id")), None)
    bid = args.id or slug_id(name)
    block = render_block(bid, name, primary, address=args.address or res["address"] or "",
                         stories=args.stories, listing_url=args.url,
                         fallback=fallback, enrich=enrich if primary.type != "securecafe" else None)
    print("\nconfig entry:")
    print(block.rstrip())
    if args.stories is None:
        print("  note: no `stories`, so alerts will say \"height unknown\". Add --stories N to label it.")
    if args.dry_run:
        print("\n(dry run, nothing written)")
        return 0
    try:
        ce.edit(path, lambda t: ce.append_building(t, bid, block), expect_buildings=len(cfg.buildings) + 1)
    except ce.ConfigEditError as e:
        print(f"\nnot added: {e}")
        return 1
    print(f"\nadded {bid} to {path.name}")
    print(f"  check it with: aptbot once -b {bid} --all-units")
    _maybe_restart(args)
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

    p = sub.add_parser("filters", help="show or change the search criteria")
    p.add_argument("--bedrooms", help="comma separated, e.g. 0,1,2 (0 = studio)")
    p.add_argument("--max-price", type=float, dest="max_price")
    p.add_argument("--min-price", type=float, dest="min_price")
    p.add_argument("--min-sqft", type=int, dest="min_sqft")
    p.add_argument("--available-by", dest="available_by", help="ISO date, only alert if available on or before")
    p.add_argument("--clear", action="append", help="remove a filter, e.g. --clear max_price")
    p.add_argument("--restart", action="store_true", help="restart the running bot afterwards")

    p = sub.add_parser("recipients", help="who gets texted")
    p.add_argument("action", nargs="?", choices=["list", "add", "remove", "set"], default="list")
    p.add_argument("numbers", nargs="*")
    p.add_argument("--restart", action="store_true")

    p = sub.add_parser("add-building", help="detect a building's leasing platform from its URL and add it")
    p.add_argument("url")
    p.add_argument("--id", help="config key; defaults to a slug of the name")
    p.add_argument("--name")
    p.add_argument("--address", default="")
    p.add_argument("--stories", type=int, help="floor count, used for the high/mid/low-rise label")
    p.add_argument("--dry-run", action="store_true", help="show what would be added, write nothing")
    p.add_argument("--force", action="store_true", help="add even if no units came back")
    p.add_argument("--restart", action="store_true")

    for name in ("enable", "disable"):
        p = sub.add_parser(name, help=f"{name} a building")
        p.add_argument("building")
        p.add_argument("--restart", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, args.verbose)
    handler: Any = {
        "run": cmd_run, "once": cmd_once, "test-notify": cmd_test_notify, "status": cmd_status,
        "list": cmd_list, "reset": cmd_reset, "resolve": cmd_resolve,
        "filters": cmd_filters, "recipients": cmd_recipients, "add-building": cmd_add_building,
        "enable": cmd_toggle, "disable": cmd_toggle,
    }[args.cmd]
    if asyncio.iscoroutinefunction(handler):
        return asyncio.run(handler(args, cfg))
    return handler(args, cfg)
