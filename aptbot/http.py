"""Async HTTP client built on curl_cffi with Chrome TLS impersonation.

Several leasing sites (securecafe.com, Cloudflare-fronted property sites, Vercel-protected Essex)
challenge plain curl/python clients; Chrome impersonation passes those checks.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession

log = logging.getLogger("aptbot.http")

DEFAULT_TIMEOUT = 40
DEFAULT_IMPERSONATE = "chrome"


class HttpError(Exception):
    def __init__(self, url: str, status: int, body: str = ""):
        self.url, self.status, self.body = url, status, body
        super().__init__(f"HTTP {status} for {url}")


class Http:
    """One shared session + per-host concurrency limits + retries."""

    def __init__(self, per_host: int = 2, total: int = 8, timeout: int = DEFAULT_TIMEOUT):
        self._session: Optional[AsyncSession] = None
        self._host_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))
        self._total = asyncio.Semaphore(total)
        self.timeout = timeout

    async def __aenter__(self) -> "Http":
        self._session = AsyncSession(impersonate=DEFAULT_IMPERSONATE, timeout=self.timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate=DEFAULT_IMPERSONATE, timeout=self.timeout)
        return self._session

    async def get(self, url: str, *, retries: int = 2, headers: Optional[dict[str, str]] = None,
                  allow_status: tuple[int, ...] = ()) -> Any:
        host = urlsplit(url).netloc
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                async with self._total, self._host_sems[host]:
                    resp = await self.session.get(url, headers=headers or {}, allow_redirects=True)
                if resp.status_code >= 400 and resp.status_code not in allow_status:
                    raise HttpError(url, resp.status_code, resp.text[:300])
                return resp
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < retries:
                    delay = (1.5 ** attempt) + random.random()
                    log.debug("retry %s in %.1fs after %r", url, delay, e)
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def text(self, url: str, **kw: Any) -> str:
        resp = await self.get(url, **kw)
        return resp.text

    async def json(self, url: str, **kw: Any) -> Any:
        resp = await self.get(url, **kw)
        return resp.json()
