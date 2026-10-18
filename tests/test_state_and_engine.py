import asyncio
import datetime as dt

import pytest

from aptbot.config import Building, Config, Filters
from aptbot.engine import Engine
from aptbot.filters import matches
from aptbot.format import alert_text
from aptbot.models import Unit, norm_unit
from aptbot.notify import Channel, Notifier
from aptbot.state import State


def mk(bid="b1", n="101", beds=2, price=7000.0, **kw):
    return Unit(building_id=bid, building_name="B1", unit_number=n, bedrooms=beds, price=price, listing_url="https://x/l", apply_url="https://x/a", **kw)


def test_norm_unit():
    assert norm_unit("#0219") == norm_unit("219") == norm_unit("APT 0219") == "219"
    assert norm_unit("22E") == "22E" and norm_unit("UNIT 0507") == "507"


def test_filters():
    f = Filters(bedrooms=[2, 3], max_price=8000)
    assert matches(mk(), f) and not matches(mk(beds=1), f) and not matches(mk(price=9000), f)
    assert not matches(mk(beds=None), f) and matches(mk(beds=None), Filters(include_unknown_bedrooms=True))
    assert not matches(mk(available="2027-01-01"), Filters(available_by="2026-12-31"))
    assert matches(mk(available="Now"), Filters(available_by="2026-12-31"))


def test_state_new_relist_gone(tmp_path):
    st = State(tmp_path / "s.sqlite")
    r = st.reconcile("b1", [mk(n="101"), mk(n="102")])
    assert {u.unit_number for u in r["new"]} == {"101", "102"} and not r["gone"]
    r = st.reconcile("b1", [mk(n="101")])
    assert not r["new"] and [u.unit_number for u in r["gone"]] == ["102"]
    # comes back quickly -> not new, not relisted
    r = st.reconcile("b1", [mk(n="101"), mk(n="0102")])  # same unit, different formatting
    assert not r["new"] and not r["relisted"]
    # simulate it vanished long ago
    st.db.execute("UPDATE units SET active=0, gone_at=? WHERE key='b1:102'",
                  ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat(),))
    r = st.reconcile("b1", [mk(n="101"), mk(n="102")], relist_days=21)
    assert [u.unit_number for u in r["relisted"]] == ["102"]
    st.mark_notified(["b1:101"])
    assert st.was_notified("b1:101") and not st.was_notified("b1:102")
    assert st.counts()["active"] == 2
    assert len(st.active_units("b1")) == 2


def test_alert_text():
    soon = dt.date.today() + dt.timedelta(days=30)
    u = mk(sqft=1102, floor="24", bathrooms=2, available=soon.isoformat())
    u.extra.update(building_type="high-rise", building_label="High-rise (40 floors)")
    t = alert_text(u)
    assert "NEW 2bd at B1 — High-rise (40 floors)" in t and "Unit 101" in t and "$7,000/mo" in t
    assert f"Earliest move-in: {soon.strftime('%b')} {soon.day}" in t and "Apply: https://x/a" in t
    u.available = "Now"
    assert "Earliest move-in: now" in alert_text(u)
    u.available = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    assert "Earliest move-in: now" in alert_text(u)  # a past date means available now
    u.price = None
    assert "Price: call for details" in alert_text(u)
    u.extra.update(building_type="mid-rise", building_label="Mid-rise (8 floors)")
    assert "Mid-rise (8 floors)" in alert_text(u)


def test_building_classification():
    assert Building(id="a", name="a", source={}, stories=40).classification == "high-rise"
    assert Building(id="a", name="a", source={}, stories=12).classification == "high-rise"
    assert Building(id="a", name="a", source={}, stories=8).classification_label == "Mid-rise (8 floors)"
    assert Building(id="a", name="a", source={}, stories=3).classification == "low-rise"
    assert Building(id="a", name="a", source={}).classification_label == "height unknown"
    assert Building(id="a", name="a", source={}, stories=3, building_type="high-rise").classification == "high-rise"


class FakeSource:
    def __init__(self):
        self.units = []
        self.fail = False

    async def fetch(self, http, b, cfg=None):
        if self.fail:
            raise RuntimeError("boom")
        return list(self.units)


class Capture(Channel):
    name = "capture"

    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_engine_baseline_then_alert(tmp_path, monkeypatch):
    src = FakeSource()
    monkeypatch.setattr("aptbot.engine.get_source", lambda kind: src)
    b = Building(id="b1", name="B1", source={"type": "fake"})
    cfg = Config(buildings=[b], filters=Filters(), notify={}, poll={"relist_days": 21},
                 paths={"db": tmp_path / "s.sqlite", "status": tmp_path / "status.json", "current": tmp_path / "cur.md", "logs": tmp_path},
                 raw={})
    st = State(cfg.paths["db"])
    cap = Capture()
    eng = Engine(cfg, st, http=None, notifier=Notifier([cap]))
    src.units = [mk(n="101"), mk(n="201", beds=1)]
    r = await eng.poll_building(b)
    assert r["ok"] and r["baseline"] and r["matches"] == 1 and cap.sent == []  # baseline: no alerts
    src.units.append(mk(n="102", beds=3))
    r = await eng.poll_building(b)
    assert r["new"] == 1 and r["alerted"] == 1 and len(cap.sent) == 1 and "Unit 102" in cap.sent[0]
    # a new studio should not alert
    src.units.append(mk(n="103", beds=0))
    r = await eng.poll_building(b)
    assert r["new"] == 1 and r["alerted"] == 0 and len(cap.sent) == 1
    # fetch failure must not mark units gone
    src.fail = True
    r = await eng.poll_building(b)
    assert not r["ok"] and st.counts()["active"] == 4
    src.fail = False
    assert (tmp_path / "status.json").exists()
    assert eng.write_current_matches() == 2


def test_contact_only_alert_wording():
    u = mk(sqft=1506, bathrooms=2, beds=3, available=(dt.date.today() + dt.timedelta(days=30)).isoformat())
    u.extra.update(building_type="high-rise", building_label="High-rise (40 floors)",
                   contact_only=True, phone="(628) 258-0126")
    u.apply_url = "https://x/contactusdialog.aspx?myUnitId=1"
    t = alert_text(u)
    assert "not open online yet" in t and "Call (628) 258-0126." in t and "Contact form: https://x/contactusdialog.aspx" in t
    u.extra.pop("contact_only")
    assert "Apply: https://x/contactusdialog.aspx?myUnitId=1" in alert_text(u)
    assert alert_text(u, now_applyable=True).startswith("\U0001F3D9\ufe0f NOW APPLYABLE")


@pytest.mark.asyncio
async def test_engine_alerts_when_application_opens(tmp_path, monkeypatch):
    src = FakeSource()
    monkeypatch.setattr("aptbot.engine.get_source", lambda kind: src)
    b = Building(id="b1", name="B1", source={"type": "fake"})
    cfg = Config(buildings=[b], filters=Filters(), notify={}, poll={"relist_days": 21},
                 paths={"db": tmp_path / "s.sqlite", "status": tmp_path / "status.json", "current": tmp_path / "cur.md", "logs": tmp_path},
                 raw={})
    st = State(cfg.paths["db"])
    cap = Capture()
    eng = Engine(cfg, st, http=None, notifier=Notifier([cap]))
    blocked = mk(n="2801", beds=3)
    blocked.extra["contact_only"] = True
    src.units = [blocked]
    await eng.poll_building(b)          # baseline, no alert
    assert cap.sent == []
    src.units = [mk(n="2801", beds=3)]  # property published a price
    r = await eng.poll_building(b)
    assert r["now_applyable"] == 1 and len(cap.sent) == 1 and "NOW APPLYABLE" in cap.sent[0]
    r = await eng.poll_building(b)      # stays open, no repeat
    assert r["now_applyable"] == 0 and len(cap.sent) == 1
