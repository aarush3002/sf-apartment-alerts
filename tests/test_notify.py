import pytest

from aptbot.notify.imessage import IMessageChannel, normalize_number


def test_normalize_number():
    assert normalize_number("5551234567") == "+15551234567"
    assert normalize_number("(555) 123-4567") == "+15551234567"
    assert normalize_number("+1 555 123 4567") == "+15551234567"
    assert normalize_number("someone@icloud.com") == "someone@icloud.com"


@pytest.mark.asyncio
async def test_imessage_invokes_osascript(monkeypatch):
    calls = []

    async def fake_run(script, *args, timeout=30):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr("aptbot.notify.imessage._run_osascript", fake_run)
    ch = IMessageChannel("5551234567")
    await ch.send("hello")
    assert calls == [("+15551234567", "hello", "imessage")]


@pytest.mark.asyncio
async def test_imessage_falls_back_to_sms(monkeypatch):
    calls = []

    async def fake_run(script, *args, timeout=30):
        calls.append(args[2])
        return (0, "", "") if args[2] == "sms" else (1, "", "error")

    monkeypatch.setattr("aptbot.notify.imessage._run_osascript", fake_run)
    await IMessageChannel("5551234567").send("hello")
    assert calls[-1] == "sms" and "imessage" in calls


def test_build_channels_multiple_recipients():
    from aptbot.notify import build_channels
    chans = build_channels({"to": ["+15551234567", "(555) 987-6543"], "channels": ["imessage"]})
    assert [c.name for c in chans] == ["imessage:+15551234567", "imessage:+15559876543"]
    chans = build_channels({"to": "+15551234567, 5559876543", "channels": [{"type": "imessage"}]})
    assert [c.to for c in chans] == ["+15551234567", "+15559876543"]
