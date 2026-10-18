"""Twilio SMS via REST (no SDK). Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM in .env."""
from __future__ import annotations

import logging

from curl_cffi.requests import AsyncSession

from . import Channel
from .imessage import normalize_number

log = logging.getLogger("aptbot.notify.twilio")


class TwilioChannel(Channel):
    name = "twilio"

    def __init__(self, sid: str, token: str, from_number: str, to: str):
        if not to:
            raise ValueError("twilio channel needs a recipient")
        self.sid, self.token, self.from_number, self.to = sid, token, normalize_number(from_number), normalize_number(to)
        self.name = f"twilio:{self.to}"

    async def send(self, text: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        async with AsyncSession() as s:
            r = await s.post(url, data={"From": self.from_number, "To": self.to, "Body": text}, auth=(self.sid, self.token), timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Twilio HTTP {r.status_code}: {r.text[:300]}")
