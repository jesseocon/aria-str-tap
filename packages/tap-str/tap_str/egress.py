"""Log the browser's outbound IP address during a run.

Testing a theory (2026-10-27): CoStar ends a session about 5½ minutes after sign-in in
roughly 1 run in 5, while the page still looks signed in. Cloud Run has no fixed outbound
address, so if CoStar ties a session to the address it signed in from, a change of address
mid-run would explain it. This records the address at sign-in, about once a minute, and at
any SessionExpired, so failed and successful runs can be compared. It asks a public
"what's my IP" service through the browser's own request context; nothing goes to CoStar.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

IP_URL = "https://api.ipify.org?format=json"
EVERY_SECONDS = 60


class EgressWatch:
    def __init__(self, logger: logging.Logger, *, every_seconds: float = EVERY_SECONDS) -> None:
        self.logger = logger
        self.every_seconds = every_seconds
        self.last_ip: str | None = None
        self.started: float | None = None
        self._last_check = 0.0

    def _lookup(self, page: Page) -> str | None:
        try:
            response = page.context.request.get(IP_URL, timeout=10_000)
            return str(response.json().get("ip") or "") or None
        except Exception as exc:  # noqa: BLE001 - evidence only; never affects the run
            self.logger.info("Outbound IP lookup failed: %s", str(exc).splitlines()[0][:200])
            return None

    def record(self, page: Page, moment: str) -> str | None:
        """Log the address now, and whether it changed since the last check."""
        now = time.monotonic()
        self._last_check = now
        if self.started is None:
            self.started = now
        ip = self._lookup(page)
        if ip is None:
            return None
        minutes = (now - self.started) / 60
        if self.last_ip is None:
            self.logger.info("Outbound IP %s: %s", moment, ip)
        elif ip != self.last_ip:
            self.logger.warning("Outbound IP changed %s (%.1f min in): %s -> %s", moment, minutes, self.last_ip, ip)
        else:
            self.logger.info("Outbound IP %s (%.1f min in): %s (unchanged)", moment, minutes, ip)
        self.last_ip = ip
        return ip

    def maybe_record(self, page: Page) -> None:
        if time.monotonic() - self._last_check >= self.every_seconds:
            self.record(page, "check")
