"""Parse the STR daily page's PortfolioListingAll responses into rows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# The daily page's Last Year window is the same weekdays, 364 days earlier.
LAST_YEAR_SHIFT_DAYS = 364

KEEP_OPERATIONS = frozenset({"PortfolioListingAll", "PropertyDetailV2", "PropertySetMembers"})

# Daily dataPt series only. Week and month are summed later from supply, demand, and revenue.
DAILY_METRICS = {
    "supply": "supply",
    "demand": "demand",
    "revenue": "revenue",
    "occupancy": "occupancy",
    "adr": "adr",
    "revPar": "revpar",
    "compSetSupply": "comp_set_supply",
    "compSetDemand": "comp_set_demand",
    "compSetRevenue": "comp_set_revenue",
    "compSetOccupancy": "comp_set_occupancy",
    "compSetAdr": "comp_set_adr",
    "compSetRevPar": "comp_set_revpar",
    "occupancyIndex": "occupancy_index",
    "adrIndex": "adr_index",
    "revParIndex": "revpar_index",
}


def shift_date(iso_date: str, days: int = LAST_YEAR_SHIFT_DAYS) -> str:
    """Move a YYYY-MM-DD date by ``days``."""
    return (date.fromisoformat(iso_date[:10]) + timedelta(days=days)).isoformat()


def decode_date_key(key: str) -> str | None:
    """Decode a STR day key such as 2026241009 into 2026-10-09.

    The key is YYYY, then month+14, then MMDD.
    """
    if len(key) != 10 or not key.isdigit():
        return None
    year = int(key[0:4])
    month = int(key[4:6]) - 14
    month_from_date = int(key[6:8])
    day = int(key[8:10])
    if month != month_from_date or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def records_from_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join subject, comp set, and index series on stay date.

    The page sends one date window, then a second window when Last Year is on.
    The first window is ``current``. A later window is ``last_year``.
    """
    dimensions = _dimensions(calls)
    windows: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for call in calls:
        if call.get("operation") != "PortfolioListingAll":
            continue
        payload = (call.get("variables") or {}).get("input") or {}
        begin = str(payload.get("beginDate") or "")
        if not begin:
            continue
        window = windows.get(begin)
        if window is None:
            window = {"input": payload, "metrics": {}}
            windows[begin] = window
            order.append(begin)
        series = ((call.get("data") or {}).get("PortfolioListingAll") or {}).get("data") or {}
        if isinstance(series, dict):
            window["metrics"].update(series)

    rows: list[dict[str, Any]] = []
    for index, begin in enumerate(order):
        window = windows[begin]
        by_date: dict[str, dict[str, Any]] = {}
        for name, points in window["metrics"].items():
            column = DAILY_METRICS.get(name)
            if column is None or not isinstance(points, dict):
                continue
            for key, point in points.items():
                stay_date = decode_date_key(str(key))
                if stay_date is None or not isinstance(point, dict) or "dataPt" not in point:
                    continue
                by_date.setdefault(stay_date, {})[column] = point["dataPt"]
        begin_date = begin[:10]
        end_date = str(window["input"].get("endDate") or "")[:10]
        label = "current" if index == 0 else "last_year"
        for stay_date in sorted(by_date):
            rows.append(
                {
                    "property_id": dimensions.get("property_id"),
                    "stay_date": stay_date,
                    "window": label,
                    "begin_date": begin_date,
                    "end_date": end_date,
                    **{key: value for key, value in dimensions.items() if key != "property_id"},
                    **by_date[stay_date],
                },
            )
    return rows


def _dimensions(calls: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions: dict[str, Any] = {}
    for call in calls:
        data = call.get("data") or {}
        if call.get("operation") == "PropertyDetailV2":
            detail = data.get("propertyDetailV2") or {}
            prop = detail.get("property") or {}
            context = detail.get("SecurePropertyContext") or {}
            comp_sets = data.get("compsetList") or []
            comp_set = comp_sets[0] if isinstance(comp_sets, list) and comp_sets else {}
            dimensions.update(
                {
                    "property_id": _text(context.get("id")),
                    "hotel_name": prop.get("hotelName"),
                    "str_id": _text(context.get("strId")),
                    "num_rooms": prop.get("numRooms"),
                    "brand_name": prop.get("brandName"),
                    "market": prop.get("market"),
                    "submarket": prop.get("submarket"),
                    "address": prop.get("address"),
                    "owner_name": _first_name(prop.get("owners")),
                    "operator_name": _first_name(prop.get("operators")),
                    "comp_set_id": _text(comp_set.get("propertySetId")),
                    "comp_set_name": comp_set.get("name"),
                    "comp_set_property_count": comp_set.get("propertyCount"),
                    "comp_set_room_count": comp_set.get("roomCount"),
                },
            )
        if call.get("operation") == "PropertySetMembers":
            members = data.get("propertySetMembers") or {}
            if members.get("propertySetId"):
                dimensions["comp_set_id"] = _text(members["propertySetId"])
    if not dimensions.get("property_id"):
        for call in calls:
            if call.get("operation") != "PortfolioListingAll":
                continue
            payload = (call.get("variables") or {}).get("input") or {}
            if payload.get("idType") == "Property" and payload.get("ids"):
                dimensions["property_id"] = _text(payload["ids"][0])
                break
    return {key: value for key, value in dimensions.items() if value is not None}


def _text(value: Any) -> str | None:  # noqa: ANN401
    if value is None:
        return None
    return str(value)


def _first_name(items: Any) -> str | None:  # noqa: ANN401
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    name = items[0].get("name")
    return str(name) if name else None
