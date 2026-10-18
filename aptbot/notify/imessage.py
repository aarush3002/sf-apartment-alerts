"""Send through the Mac's Messages.app (iMessage, falling back to SMS relay if the iPhone has
Text Message Forwarding enabled). No API keys needed; requires Messages.app signed in.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
from typing import Optional

from . import Channel

log = logging.getLogger("aptbot.notify.imessage")

# Modern (macOS 13+) object model first, legacy `buddy`/`service` model second.
SCRIPT_MODERN = '''
on run argv
  set theTarget to item 1 of argv
  set theText to item 2 of argv
  set theKind to item 3 of argv
  tell application "Messages"
    if theKind is "sms" then
      set theAccount to 1st account whose service type = SMS
    else
      set theAccount to 1st account whose service type = iMessage
    end if
    set theParticipant to participant theTarget of theAccount
    send theText to theParticipant
  end tell
end run
'''
SCRIPT_LEGACY = '''
on run argv
  set theTarget to item 1 of argv
  set theText to item 2 of argv
  set theKind to item 3 of argv
  tell application "Messages"
    if theKind is "sms" then
      set theService to 1st service whose service type = SMS
    else
      set theService to 1st service whose service type = iMessage
    end if
    set theBuddy to buddy theTarget of theService
    send theText to theBuddy
  end tell
end run
'''


def normalize_number(n: str) -> str:
    n = n.strip()
    if "@" in n:
        return n  # Apple ID email
    digits = re.sub(r"\D", "", n)
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


async def _run_osascript(script: str, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-", *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(script.encode()), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("osascript timed out (is Messages.app waiting on a permission dialog?)")
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class IMessageChannel(Channel):
    name = "imessage"

    def __init__(self, to: str, prefer: str = "auto"):
        if not to:
            raise ValueError("imessage channel needs a recipient (notify.to)")
        self.to = normalize_number(to)
        self.prefer = prefer  # auto | imessage | sms
        self.name = f"imessage:{self.to}"

    async def send(self, text: str) -> None:
        kinds = ["imessage", "sms"] if self.prefer == "auto" else [self.prefer]
        errors = []
        for kind in kinds:
            for script in (SCRIPT_MODERN, SCRIPT_LEGACY):
                rc, out, err = await _run_osascript(script, self.to, text, kind)
                if rc == 0:
                    return
                errors.append(f"{kind}: {err.strip() or out.strip()}")
        raise RuntimeError("Messages.app send failed: " + " | ".join(errors[-2:]))

    def __repr__(self) -> str:
        return f"IMessageChannel(to={self.to}, prefer={self.prefer})"
