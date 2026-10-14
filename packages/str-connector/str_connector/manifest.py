"""Connector manifest rendered by the Aria console."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

KEY = "str"
STREAMS = ("daily",)
DATE = {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
ENVIRONMENTS = ("development", "production")


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
        "auth": {
            "kind": "password",
            "fields": [
                {
                    "key": "username",
                    "title": "Username",
                    "secret": False,
                    "env": "TAP_STR_USERNAME",
                },
                {
                    "key": "password",
                    "title": "Password",
                    "secret": True,
                    "env": "TAP_STR_PASSWORD",
                },
            ],
        },
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
                    "default": ["daily"],
                },
                "begin_date": {**DATE, "title": "First stay date", "default": "2025-08-01"},
                "end_date": {**DATE, "title": "Last stay date", "default": "2026-10-09"},
                "include_last_year": {
                    "type": "boolean",
                    "title": "Include last year (same weekdays)",
                    "default": True,
                },
            },
        },
        "run_params_schema": {"type": "object", "additionalProperties": False},
    }
