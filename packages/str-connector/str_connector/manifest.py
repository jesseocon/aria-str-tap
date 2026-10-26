"""Connector manifest rendered by the Aria console."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

KEY = "str"
# STAR-tab streams. Every login lands on the same STR app, so every connection syncs these.
STREAMS = ("str_series", "comp_set_members", "comp_set_response")
# Matches tap_str.community.plan.VIEW_NAMES.
COMMUNITY_VIEWS = (
    "daily",
    "summary",
    "monthly",
    "day_of_week_industry",
    "day_of_week_comp_set",
    "seg_industry",
    "seg_comp_set",
    "seg_index",
    "seg_day_of_week_industry",
    "seg_day_of_week_comp_set",
    "additional_revenue_industry",
    "additional_revenue_comp_set",
    "add_rev_weekly",
    "seg_weekly",
    "combined_summary_weekly",
)
DATE = {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
ENVIRONMENTS = ("development", "production")
# Gold marts written as {mart}_{property_id}; aria-runner keeps gold_{org}.{mart} as a portfolio view.
PORTFOLIO_MARTS = (
    "mlt_mart__benchmark__daily",
    "mlt_mart__benchmark__period",
    "mlt_mart__benchmark__segment",
    "mlt_mart__benchmark__day_of_week",
    "mlt_mart__benchmark__comp_set_coverage",
)

DIRECT_AUTH = {
    "kind": "password",
    "fields": [
        {"key": "username", "title": "Username", "secret": False, "env": "TAP_STR_USERNAME"},
        {"key": "password", "title": "Password", "secret": True, "env": "TAP_STR_PASSWORD"},
    ],
}
HILTON_LOBBY_AUTH = {
    "kind": "password",
    "fields": [
        {"key": "user_id", "title": "Hilton User ID", "secret": False, "env": "TAP_STR_PORTAL_USERNAME"},
        {"key": "password", "title": "Hilton password", "secret": True, "env": "TAP_STR_PORTAL_PASSWORD"},
        {
            "key": "totp_secret",
            "title": "Authenticator secret key",
            "secret": True,
            "env": "TAP_STR_PORTAL_TOTP_SECRET",
        },
    ],
}


def _version() -> str:
    try:
        return version("str-connector")
    except PackageNotFoundError:
        return "0.0.0"


def manifest() -> dict[str, Any]:
    return {
        "key": KEY,
        "display_name": "CoStar STR",
        "logo": "/logos/str.png",
        "version": _version(),
        "description": "CoStar STR daily occupancy, ADR, RevPAR, comp set, and index.",
        "job": {"name": "aria-connector-str", "timeout_seconds": 3 * 3600},
        "progress": "v1",
        # Ways in to the same STR app. The first option is the default for connections saved before options.
        "auth_options": [
            {"key": "direct", "title": "CoStar username and password", "auth": DIRECT_AUTH},
            {
                "key": "hilton_lobby",
                "title": "Hilton Lobby (SSO with authenticator code)",
                "auth": HILTON_LOBBY_AUTH,
            },
        ],
        "portfolio_marts": list(PORTFOLIO_MARTS),
        "config_schema": {
            "type": "object",
            "title": "STR property",
            "additionalProperties": False,
            "required": ["org_id", "property_id", "development_property_id", "streams"],
            "properties": {
                "org_id": {
                    "type": "string",
                    "title": "Organization id",
                    "pattern": r"^\d+$",
                },
                "property_id": {
                    "type": "string",
                    "title": "CoStar property id",
                    "default": "4603810",
                    "pattern": r"^\d+$",
                },
                "development_property_id": {
                    "type": "string",
                    "title": "Aria property id (development)",
                },
                "production_property_id": {
                    "type": "string",
                    "title": "Aria property id (production)",
                },
                "environments": {
                    "type": "array",
                    "title": "Warehouses",
                    "items": {"enum": list(ENVIRONMENTS)},
                    "minItems": 1,
                    "uniqueItems": True,
                    "default": ["development"],
                },
                "streams": {
                    "type": "array",
                    "title": "Streams",
                    "items": {"enum": list(STREAMS)},
                    "minItems": 1,
                    "uniqueItems": True,
                    "default": list(STREAMS),
                },
                "begin_date": {**DATE, "title": "First stay date for daily views", "default": "2025-08-01"},
                "monthly_begin_date": {
                    **DATE,
                    "title": "First month for monthly views",
                    "default": "2024-01-01",
                },
                "community_views": {
                    "type": "array",
                    "title": "STAR views to pull. Empty means all.",
                    "items": {"enum": list(COMMUNITY_VIEWS)},
                    "uniqueItems": True,
                },
                "fiscal_year_start_month": {
                    "type": "integer",
                    "title": "Fiscal year start month (for YTD benchmarks)",
                    "minimum": 1,
                    "maximum": 12,
                    "default": 1,
                },
                "comp_set_id": {
                    "type": "string",
                    "title": "STR comp set id. Empty means the performance set.",
                    "pattern": r"^\d*$",
                },
            },
        },
        "run_params_schema": {"type": "object", "additionalProperties": False},
    }
