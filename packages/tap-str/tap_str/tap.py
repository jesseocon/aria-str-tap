"""Str tap."""

from __future__ import annotations

import sys

from singer_sdk import typing as th

from singer_playwright.tap import PlaywrightTap
from tap_str import streams

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
            description="First stay date, sent as PortfolioListingAll beginDate.",
        ),
        th.Property(
            "end_date",
            th.StringType(nullable=False),
            default="2026-10-09",
            description="Last stay date, sent as PortfolioListingAll endDate.",
        ),
        th.Property(
            "include_last_year",
            th.BooleanType(nullable=False),
            default=True,
            description="Also fetch the same weekdays 364 days earlier.",
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

    @override
    def discover_streams(self) -> list[streams.DailyStream]:
        return [streams.DailyStream(self)]


if __name__ == "__main__":
    TapStr.cli()
