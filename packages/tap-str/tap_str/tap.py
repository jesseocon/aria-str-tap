"""Str tap."""

from __future__ import annotations

import sys

from singer_sdk import typing as th

from singer_playwright.browser import BrowserConfig, BrowserRuntime
from singer_playwright.progress import report
from singer_playwright.screenshots import screenshot
from singer_playwright.sso_login import SsoCredentials
from singer_playwright.tap import PlaywrightTap
from tap_str import portals, signout
from tap_str.client import StrStream
from tap_str.community import streams as community

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override


class TapStr(PlaywrightTap):
    name = "tap-str"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "auth_method",
            th.StringType(nullable=False),
            allowed_values=["storage_state", "password"],
            default="password",
            description="password types the username and password in this browser. storage_state replays a saved login.",
        ),
        th.Property(
            "login_url",
            th.StringType(nullable=False),
            default="https://product.costar.com/",
            description="Page that starts login when auth_method is password.",
        ),
        th.Property(
            "username",
            th.StringType(nullable=True),
            secret=True,
            description="CoStar username. Required when auth_method is password.",
        ),
        th.Property(
            "password",
            th.StringType(nullable=True),
            secret=True,
            description="CoStar password. Required when auth_method is password.",
        ),
        th.Property(
            "property_id",
            th.StringType(nullable=False),
            default="4603810",
            description="CoStar property id from the STR detail URL.",
        ),
        th.Property(
            "begin_date",
            th.StringType(nullable=False),
            default="2025-08-01",
            description="First stay date for daily views. monthly_begin_date is the first month for monthly views.",
        ),
        th.Property(
            "access_path",
            th.StringType(nullable=False),
            allowed_values=["direct", *portals.PORTALS],
            default="direct",
            description="direct signs in to CoStar. hilton_lobby goes through Hilton SSO (password + TOTP) and OnQ My Applications.",
        ),
        th.Property(
            "portal_username",
            th.StringType(nullable=True),
            secret=True,
            description="Brand portal SSO username (e.g. the Hilton email). Used when access_path is a portal.",
        ),
        th.Property(
            "portal_password",
            th.StringType(nullable=True),
            secret=True,
            description="Brand portal SSO password.",
        ),
        th.Property(
            "portal_totp_secret",
            th.StringType(nullable=True),
            secret=True,
            description="Base32 secret of an authenticator app registered on the portal account.",
        ),
        th.Property(
            "community_views",
            th.ArrayType(th.StringType),
            nullable=True,
            description="STAR-tab views to pull into str_series. Default: all of them.",
        ),
        th.Property(
            "monthly_begin_date",
            th.StringType(nullable=True),
            default="2024-01-01",
            description="Earliest month for monthly views. begin_date is the earliest day for daily data.",
        ),
        th.Property(
            "fiscal_year_start_month",
            th.IntegerType(nullable=False),
            default=1,
            description="Month the fiscal year starts, for STR's YTD figures.",
        ),
        th.Property(
            "comp_set_id",
            th.StringType(nullable=True),
            description="STR comp set (propertySetId). Default: the performance set.",
        ),
        th.Property(
            "base_url",
            th.StringType(nullable=False),
            default="https://www.costar.com/",
        ),
        th.Property(
            "headless",
            th.BooleanType(nullable=False),
            default=False,
        ),
        th.Property(
            "rate_limit_seconds",
            th.NumberType(nullable=False),
            default=5.0,
        ),
    ).to_dict()

    _portal_page = None

    @override
    def _ensure_browser_started(self) -> None:
        access_path = self.config.get("access_path", "direct")
        if access_path == "direct" or self._browser_runtime is not None:
            super()._ensure_browser_started()
            return
        enter = portals.PORTALS[access_path]
        missing = [k for k in ("portal_username", "portal_password", "portal_totp_secret") if not self.config.get(k)]
        if missing:
            msg = f"access_path {access_path} needs {', '.join(missing)}"
            raise ValueError(msg)
        self._browser_runtime = BrowserRuntime(
            BrowserConfig(storage_state_path=None, headless=bool(self.config.get("headless", True))),
        )
        self._browser_runtime.start()
        self._portal_page = enter(
            self._browser_runtime.page,
            SsoCredentials(
                username=str(self.config["portal_username"]),
                password=str(self.config["portal_password"]),
                totp_secret=str(self.config["portal_totp_secret"]),
            ),
        )
        self._session_verified = True

    @property
    @override
    def page(self):  # noqa: ANN201 - playwright Page
        """The CoStar tab: the portal opens STR in a new tab, not the first one."""
        self._ensure_browser_started()
        if self._portal_page is not None and not self._portal_page.is_closed():
            return self._portal_page
        return super().page

    @override
    def sync_all(self) -> None:
        # singer-sdk has no teardown hook, so sign out here whether the sync finishes or fails.
        try:
            super().sync_all()
        finally:
            self.teardown()

    @override
    def teardown(self) -> None:
        runtime = self._browser_runtime
        # Every run's log says what happened to its CoStar session, success or not.
        if runtime is not None and self._session_verified:
            page = self._portal_page if self._portal_page is not None and not self._portal_page.is_closed() else runtime.page
            try:
                how = signout.sign_out(page)
                self.logger.info("Signed out of CoStar (%s)", how)
            except Exception as exc:  # noqa: BLE001 - closing the browser still has to happen
                message = f"Signing out of CoStar failed, closing the browser anyway: {exc}"
                self.logger.warning("%s", message)
                screenshot(page, "CoStar sign-out failed", self.logger)
                report(self.logger, "warning", message=message, code="sign_out_failed")
        else:
            self.logger.info("Signed out of CoStar (never signed in; nothing to sign out)")
        self._portal_page = None
        # PlaywrightTap.teardown ends in super().teardown(), which singer-sdk's Tap does not have.
        if runtime is not None:
            runtime.stop()
            self._browser_runtime = None
            self._session_verified = False

    @override
    def discover_streams(self) -> list[StrStream]:
        return [
            community.SeriesStream(self),
            community.CompSetMembersStream(self),
            community.CompSetResponseStream(self),
        ]


if __name__ == "__main__":
    TapStr.cli()
