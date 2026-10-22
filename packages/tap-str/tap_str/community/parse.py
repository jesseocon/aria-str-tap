"""Turn STAR-tab GraphQL responses into rows.

Every time-series view returns blocks shaped like
``{timeIndex: [key, ...], data: [{benchmarkingDataSliceType, dataCalculationType,
values: [...], label}], lastUpdatedDate}``. ``values[i]`` belongs to ``timeIndex[i]``.

Rows keep STR's raw identifiers (block, slice type, calc type, key) untouched and
add best-effort parsed columns. A name we do not recognise still lands; its parsed
columns are just null.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from tap_str.daily import decode_date_key

ENTITIES = (
    ("SubjectProperty", "subject_property"),
    ("SubmarketClass", "submarket_class"),
    ("Submarket", "submarket"),
    ("MarketClass", "market_class"),
    ("Market", "market"),
    ("CompSet", "comp_set"),
    ("Industry", "industry"),
    ("Index", "index"),
)
SEGMENTS = {
    "Total": "total",
    "Transient": "transient",
    "Group": "group",
    "Contract": "contract",
    "FoodBeverage": "food_beverage",
    "Other": "other",
    "Rooms": "rooms",
}
# Order matters: longer tokens first so "MTDMonth" is not read as "TDMonth".
WINDOWS = (
    ("TwentyEightDay", "28d"),
    ("SevenDay", "7d"),
    ("Twelvemonth", "12mo"),
    ("TwelveMonth", "12mo"),
    ("ThreeMonth", "3mo"),
    ("YTDMonth", "ytd"),
    ("MTD", "mtd"),
    ("Last4", "last4"),
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Weekday", "Weekend")
METRICS = (
    ("PercentOfTotalRevenue", "pct_of_total_revenue"),
    ("RevenuePerAvailableRoom", "revpar"),
    ("AverageDailyRate", "adr"),
    ("OpenPropertyCount", "open_property_count"),
    ("OpenRoomCount", "open_room_count"),
    ("SamplePropertyCount", "sample_property_count"),
    ("SampleRoomCount", "sample_room_count"),
    ("ParticipationPercent", "participation_pct"),
    ("RevPAR", "revpar"),
    ("ADR", "adr"),
    ("Occupancy", "occupancy"),
    ("Demand", "demand"),
    ("Supply", "supply"),
    ("Revenue", "revenue"),
)
CHECK_RE = re.compile(r"Sufficiency|Check")
YOY_RE = re.compile(r"GrowthYoy|Yoy|Change")
BLOCK_SKIP_KEYS = {"timeIndex", "data"}


def parse_slice(slice_type: str) -> tuple[str | None, str | None]:
    """``CompSetTransient`` -> (``comp_set``, ``transient``)."""
    for prefix, entity in ENTITIES:
        if slice_type.startswith(prefix):
            rest = slice_type[len(prefix) :]
            if not rest:
                return entity, None
            return entity, SEGMENTS.get(rest)
    return None, None


def parse_calc(calc: str) -> dict[str, str | None]:
    """``HotelThreeMonthADRFridayChange`` -> adr / change_yoy / 3mo / friday."""
    window = "period"
    for token, name in WINDOWS:
        if token in calc:
            window = name
            break
    else:
        if "TDMonth" in calc:  # STR typo of YTDMonth on one weekend check
            window = None
    weekday = next((day.lower() for day in WEEKDAYS if day in calc), None)
    metric = next((name for token, name in METRICS if token in calc), None)

    if CHECK_RE.search(calc):
        measure, metric = "check", None
    elif "RankPropertyCount" in calc or "RankGrowthYoyPropertyCount" in calc:
        measure = "rank_property_count_yoy" if "GrowthYoy" in calc else "rank_property_count"
        metric = None
    elif calc.endswith("Rank"):
        measure = "rank_change_yoy" if YOY_RE.search(calc) else "rank"
    elif YOY_RE.search(calc):
        measure = "change_yoy"
    else:
        measure = "value"
    return {"metric": metric, "measure": measure, "window": window, "weekday": weekday}


def _number(value: Any) -> tuple[float | None, str | None]:  # noqa: ANN401
    if value is None:
        return None, None
    if isinstance(value, (bool, int, float)):
        return float(value), None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, str(value)


def iter_blocks(data: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:  # noqa: ANN401
    """Yield (dotted path, block) for every time-series block in a response."""
    if isinstance(data, dict):
        if "timeIndex" in data and isinstance(data.get("data"), list):
            yield path.lstrip("."), data
            return
        for key, value in data.items():
            yield from iter_blocks(value, f"{path}.{key}")


def series_rows(
    data: dict[str, Any],
    *,
    view: str,
    key_grain: str,
    selected_date: str,
    mode: str | None = None,
) -> Iterator[dict[str, Any]]:
    """One row per non-null value in every block of a time-series response."""
    calc_cache: dict[str, dict[str, str | None]] = {}
    slice_cache: dict[str, tuple[str | None, str | None]] = {}
    for block_path, block in iter_blocks(data):
        keys = [str(key) for key in block.get("timeIndex") or []]
        dates = [decode_date_key(key) for key in keys]
        data_as_of = block.get("lastUpdatedDate")
        for series in block["data"]:
            if not isinstance(series, dict):
                continue
            slice_type = str(series.get("benchmarkingDataSliceType") or "")
            calc = str(series.get("dataCalculationType") or "")
            if calc not in calc_cache:
                calc_cache[calc] = parse_calc(calc)
            if slice_type not in slice_cache:
                slice_cache[slice_type] = parse_slice(slice_type)
            entity, segment = slice_cache[slice_type]
            for index, raw in enumerate(series.get("values") or []):
                if raw is None or index >= len(keys):
                    continue
                value, value_text = _number(raw)
                yield {
                    "source_view": view,
                    "block": block_path,
                    "mode": mode,
                    "selected_date": selected_date,
                    "key": keys[index],
                    "key_date": dates[index],
                    "key_grain": key_grain,
                    "slice_type": slice_type,
                    "label": _text(series.get("label")),
                    "entity": entity,
                    "segment": segment,
                    "calc_type": calc,
                    **calc_cache[calc],
                    "value": value,
                    "value_text": value_text,
                    "data_as_of": data_as_of,
                }


def process_dates(dashboard: dict[str, Any]) -> dict[str, str]:
    """``DashboardPropertyList`` -> {'month': 'YYYY-MM-DD', 'week': ..., 'day': ...}."""
    dates = (dashboard.get("DashboardPropertyList") or {}).get("currentProcessDates") or {}
    out = {}
    for key, name in (("currentProcessMonth", "month"), ("currentProcessWeek", "week"), ("currentProcessDay", "day")):
        if dates.get(key):
            out[name] = str(dates[key])[:10]
    missing = {"month", "week", "day"} - set(out)
    if missing:
        msg = f"DashboardPropertyList is missing process dates: {sorted(missing)}"
        raise ValueError(msg)
    return out


def country_code(dashboard: dict[str, Any]) -> str:
    details = (dashboard.get("DashboardPropertyList") or {}).get("propertyDetails") or []
    return str((details[0] if details else {}).get("countryCode") or "USA")


def pick_comp_set(detail: dict[str, Any], comp_set_id: str | None = None) -> dict[str, Any]:
    """The configured comp set, else the performance set, else the first one."""
    comp_sets = [c for c in detail.get("compsetList") or [] if isinstance(c, dict)]
    if not comp_sets:
        msg = "This property has no comp sets the account can see"
        raise ValueError(msg)
    if comp_set_id:
        for comp_set in comp_sets:
            if str(comp_set.get("propertySetId")) == str(comp_set_id):
                return comp_set
        msg = f"Comp set {comp_set_id} is not visible; have {[c.get('propertySetId') for c in comp_sets]}"
        raise ValueError(msg)
    return next((c for c in comp_sets if c.get("isCompSetPerformance")), comp_sets[0])


def _text(value: Any) -> str | None:  # noqa: ANN401
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:  # noqa: ANN401
    number, _ = _number(value)
    return None if number is None else int(number)


def _name(value: Any) -> str | None:  # noqa: ANN401
    if isinstance(value, dict):
        return value.get("name")
    if isinstance(value, list):
        names = [_name(item) for item in value]
        return ", ".join(n for n in names if n) or None
    return None if value is None else str(value)


def member_rows(
    members: dict[str, Any],
    details: dict[str, Any],
    *,
    comp_set: dict[str, Any],
    subject_property_id: str,
) -> Iterator[dict[str, Any]]:
    """``PropertySetMembers`` + ``PropertyDetailListV2`` -> one row per comp set hotel."""
    member_ids = [str(i) for i in (members.get("propertySetMembers") or {}).get("memberPropertyIds") or []]
    by_id = {
        str(p.get("id")): p
        for p in (details.get("PropertyDetailListV2") or {}).get("property") or []
        if isinstance(p, dict)
    }
    for member_id in [*member_ids, subject_property_id]:
        prop = by_id.get(member_id, {})
        # CoStar mixes types across hotels (hotelClass is 3 for one, "Upper Upscale" for another),
        # so every field is coerced to the stream's schema type.
        yield {
            "comp_set_id": _text(comp_set.get("propertySetId")),
            "comp_set_name": _text(comp_set.get("name")),
            "comp_set_type": _text(comp_set.get("type")),
            "property_id": member_id,
            "is_subject": member_id == subject_property_id,
            "str_id": _text(prop.get("strId")),
            "hotel_name": _text(prop.get("hotelName")),
            "brand_name": _text(prop.get("brandName")),
            "parent_company": _name(prop.get("parentCompany")),
            "owners": _name(prop.get("owners")),
            "operators": _name(prop.get("operators")),
            "num_rooms": _int(prop.get("numRooms")),
            "hotel_class": _text(prop.get("hotelClass")),
            "market": _text(prop.get("market")),
            "submarket": _text(prop.get("submarket")),
            "address": _text(prop.get("address")),
            "city": _text(prop.get("city")),
            "state": _text(prop.get("state")),
            "postal_code": _text(prop.get("postalCode")),
            "country_code": _text(prop.get("countryCode")),
            "latitude": _number(prop.get("latitude"))[0],
            "longitude": _number(prop.get("longitude"))[0],
            "open_date": _text(prop.get("openDate")),
            "operating_status": _text(prop.get("operatingStatus")),
        }


def response_rows(data: dict[str, Any], *, comp_set_id: str) -> Iterator[dict[str, Any]]:
    """``ResponseTableScalar`` -> one row per hotel, series and month (did it submit?)."""
    payload = data.get("ResponseTableScalar") or {}
    for prop in payload.get("submission") or []:
        for series in prop.get("seriesData") or []:
            for point in series.get("value") or []:
                key = str(point.get("timeIndex") or "")
                if len(key) != 8 or not key.isdigit():
                    continue
                yield {
                    "comp_set_id": comp_set_id,
                    "property_id": str(prop.get("propertyID")),
                    "census_id": None if prop.get("censusID") is None else str(prop.get("censusID")),
                    "series_name": series.get("seriesName"),
                    "period_date": f"{key[:4]}-{key[4:6]}-{key[6:]}",
                    "numeric_value": _number(point.get("numericValue"))[0],
                    "string_value": _text(point.get("stringValue")),
                }
