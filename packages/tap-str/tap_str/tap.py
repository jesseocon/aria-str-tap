"""Str tap."""

from __future__ import annotations

import sys

from singer_sdk import typing as th

from singer_playwright.tap import PlaywrightTap
from tap_str import streams
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
            "variant",
            th.StringType(nullable=False),
            allowed_values=["full", "community"],
            default="full",
            description="full is the CoStar STR product. community is the pared-down version behind brand portals such as Hilton Lobby.",
        ),
        th.Property(
            "community_views",
            th.ArrayType(th.StringType),
            nullable=True,
            description="Community STAR-tab views to pull into str_series. Default: all of them.",
        ),
        th.Property(
            "monthly_begin_date",
            th.StringType(nullable=True),
            default="2024-01-01",
            description="Community: earliest month for monthly views. begin_date is the earliest day for daily data.",
        ),
        th.Property(
            "fiscal_year_start_month",
            th.IntegerType(nullable=False),
            default=1,
            description="Community: month the fiscal year starts, for STR's YTD figures.",
        ),
        th.Property(
            "comp_set_id",
            th.StringType(nullable=True),
            description="Community: STR comp set (propertySetId). Default: the performance set.",
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
    def discover_streams(self) -> list[StrStream]:
        return [
            streams.DailyStream(self),
            community.SeriesStream(self),
            community.CompSetMembersStream(self),
            community.CompSetResponseStream(self),
        ]


if __name__ == "__main__":
    TapStr.cli()
