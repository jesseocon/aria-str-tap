"""Str stream base."""

from __future__ import annotations

import sys
from abc import abstractmethod
from typing import TYPE_CHECKING

from singer_playwright.login import PASSWORD_SELECTOR, LoginRejected
from singer_playwright.stream import PlaywrightStream

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Iterable

    from playwright.sync_api import Page
    from singer_sdk.helpers.types import Context


class StrStream(PlaywrightStream):
    """Shared extraction helpers for Str."""

    @override
    def get_records(self, context: Context | None) -> Iterable[dict]:
        try:
            yield from super().get_records(context)
        except Exception as exc:
            # An outage on CoStar's side: say so after the stream's own error report, as the
            # run's last error, with retryable so the runner queues the run again later.
            if getattr(exc, "retryable", False):
                self.report("error", message=str(exc)[:500], code="costar_unavailable", retryable=True)
            raise

    @override
    def extract_records(
        self,
        page: Page,
        context: Context | None,
    ) -> Iterable[dict]:
        password = page.locator(PASSWORD_SELECTOR)
        if password.count() > 0 and password.first.is_visible():
            raise LoginRejected("Site is still showing a password form.")
        yield from self.extract_site_records(page, context)

    @abstractmethod
    def extract_site_records(
        self,
        page: Page,
        context: Context | None,
    ) -> Iterable[dict]:
        """Implement site-specific scraping for this stream."""
