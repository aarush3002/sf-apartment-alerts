"""Notification fan-out. Channels are configured in config.yaml `notify:` and secrets in .env."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

log = logging.getLogger("aptbot.notify")


class Channel:
    name = "base"

    async def send(self, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleChannel(Channel):
    name = "console"

    async def send(self, text: str) -> None:
        print("\n" + "=" * 70 + "\n" + text + "\n" + "=" * 70, flush=True)


def _as_list(v: Any) -> list[str]:
    """Accept a single recipient, a comma-separated string, or a list."""
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def build_channels(cfg: dict[str, Any]) -> list[Channel]:
    from .imessage import IMessageChannel
    from .ntfy import NtfyChannel
    from .twilio_sms import TwilioChannel

    chans: list[Channel] = []
    default_to = _as_list(cfg.get("to") or os.environ.get("APTBOT_TO"))
    for ch in cfg.get("channels") or ["imessage"]:
        if isinstance(ch, str):
            ch = {"type": ch}
        t = ch.get("type")
        recipients = _as_list(ch.get("to")) or default_to
        if t == "imessage":
            for to in recipients:
                chans.append(IMessageChannel(to, prefer=ch.get("prefer", "auto")))
        elif t == "twilio":
            sid = os.environ.get("TWILIO_ACCOUNT_SID")
            tok = os.environ.get("TWILIO_AUTH_TOKEN")
            frm = os.environ.get("TWILIO_FROM") or ch.get("from")
            if sid and tok and frm:
                for to in recipients:
                    chans.append(TwilioChannel(sid, tok, frm, to))
            else:
                log.warning("twilio channel configured but TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM missing in .env; skipping")
        elif t == "ntfy":
            topic = ch.get("topic") or os.environ.get("NTFY_TOPIC")
            if topic:
                chans.append(NtfyChannel(topic, ch.get("server", "https://ntfy.sh")))
            else:
                log.warning("ntfy channel configured but no topic; skipping")
        elif t == "console":
            chans.append(ConsoleChannel())
        else:
            log.warning("unknown notify channel %r", t)
    return chans


class Notifier:
    def __init__(self, channels: list[Channel], required: Optional[list[str]] = None):
        self.channels = channels
        self.required = required or []

    async def send(self, text: str) -> dict[str, Optional[str]]:
        """Send to every channel; returns {channel_name: error or None}."""
        results: dict[str, Optional[str]] = {}

        async def one(ch: Channel) -> None:
            try:
                await ch.send(text)
                results[ch.name] = None
                log.info("notified via %s", ch.name)
            except Exception as e:  # noqa: BLE001
                results[ch.name] = f"{type(e).__name__}: {e}"
                log.error("notify via %s failed: %s", ch.name, e)

        await asyncio.gather(*(one(c) for c in self.channels))
        return results
