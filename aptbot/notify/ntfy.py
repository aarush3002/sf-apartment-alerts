"""ntfy.sh push notifications (free, no account): install the ntfy app and subscribe to your topic."""
from __future__ import annotations

from curl_cffi.requests import AsyncSession

from . import Channel


class NtfyChannel(Channel):
    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self.topic, self.server = topic, server.rstrip("/")

    async def send(self, text: str) -> None:
        title, _, body = text.partition("\n")
        async with AsyncSession() as s:
            r = await s.post(f"{self.server}/{self.topic}", data=body.encode(),
                             headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": "high", "Tags": "house"}, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"ntfy HTTP {r.status_code}: {r.text[:200]}")
