"""Which STAR-tab calls one run makes, and with what dates.

Cutoffs come from CoStar's own process dates, not the calendar:
- daily data ends on the last Sunday before ``currentProcessDay`` (STR publishes
  Tuesdays; Sunday is the last full day);
- monthly views end at ``currentProcessMonth``;
- weekly views end at ``currentProcessWeek`` (a Saturday; STR weeks run Sun-Sat).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from importlib import resources
from typing import Any

GRAPHQL_URL = "https://product.costar.com/app/str-mfe/api/graphql"


@dataclass(frozen=True)
class View:
    """One STAR-tab view. ``span_months`` is how many months one call returns."""

    name: str
    operation: str
    schedule: str  # "daily" | "monthly" | "weekly"
    key_grain: str  # what one timeIndex key stands for
    extra: dict[str, Any] = field(default_factory=dict)
    span_months: int = 1
    mode: str | None = None


def _modes(name: str, operation: str, span: int, **extra: Any) -> tuple[View, View]:  # noqa: ANN401
    """The same view asked for industry and for comp set (``propertyDetailMode``)."""
    return tuple(  # type: ignore[return-value]
        View(
            f"{name}_{mode.replace('-', '_')}",
            operation,
            "monthly",
            "month",
            {key: (mode if value == "{mode}" else value) for key, value in extra.items()},
            span,
            mode,
        )
        for mode in ("industry", "comp-set")
    )


VIEWS: tuple[View, ...] = (
    View("daily", "DailyTable", "daily", "day", {"interval": "Daily", "toggleType": "Daily", "includeRank": True}),
    View(
        "summary",
        "SummaryIndustryTable",
        "monthly",
        "month",
        {"propertyDetailMode": "comp-set", "interval": "Monthly", "includeRank": False},
        26,
        "comp-set",
    ),
    View("monthly", "MonthlyIndustryTable", "monthly", "month", {"interval": "Monthly"}, 18),
    *_modes("day_of_week", "DayOfWeekTable", 26, propertyDetailMode="{mode}", interval="Monthly", includeRank=False),
    *_modes("seg", "SegIndustryTable", 26, propertyDetailMode="{mode}", interval="Monthly"),
    View("seg_index", "SegIndexIndustryTable", "monthly", "month", {"interval": "Monthly", "includeRank": False}, 26),
    *_modes(
        "seg_day_of_week",
        "SegDayOfWeekTable",
        1,
        propertyDetailMode="{mode}",
        interval="Monthly",
        calculationInterval="OneMonth",
        includeRank=False,
    ),
    *_modes(
        "additional_revenue",
        "AdditionalRevenueIndustryTable",
        26,
        toggleType="{mode}",
        propertyDetailMode="{mode}",
        interval="Monthly",
        includeRank=True,
    ),
    # Weekly views return day keys around the selected week; windows live in the calc names.
    View(
        "add_rev_weekly",
        "AddRevWeeklyCompSet",
        "weekly",
        "day",
        {"toggleType": "comp-set", "interval": "Weekly", "includeRank": False},
    ),
    View("seg_weekly", "SegWeeklyCompSet", "weekly", "day", {"interval": "Weekly", "propertyDetailMode": "comp-set"}),
    View(
        "combined_summary_weekly",
        "CombinedSummaryIndustryTable",
        "weekly",
        "day",
        {"calculationInterval": "SevenDay", "toggleType": "current-week", "interval": "Weekly"},
    ),
)
VIEW_NAMES = tuple(view.name for view in VIEWS)


def query(operation: str) -> str:
    return resources.files("tap_str.community").joinpath("queries", f"{operation}.graphql").read_text()


def iso(day: date) -> str:
    return f"{day.isoformat()}T00:00:00.000Z"


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def add_months(day: date, months: int) -> date:
    """Month end ``months`` away from ``day``'s month."""
    index = day.year * 12 + day.month - 1 + months
    return month_end(date(index // 12, index % 12 + 1, 1))


def sunday_cutoff(process_day: date) -> date:
    """Last Sunday strictly before CoStar's ``currentProcessDay``."""
    day = process_day - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def daily_dates(begin: date, cutoff: date) -> list[date]:
    """One ``selectedDate`` per month: DailyTable returns the 1st through that date."""
    if begin > cutoff:
        return []
    out = []
    current = month_end(begin)
    while current < cutoff:
        out.append(current)
        current = add_months(current, 1)
    out.append(cutoff)
    return out


def monthly_dates(anchor: date, begin: date, span_months: int) -> list[date]:
    """Newest first, stepping back a full span so windows do not overlap."""
    out = []
    current = month_end(anchor)
    while True:
        out.append(current)
        window_start = add_months(current, -(span_months - 1)).replace(day=1)
        if window_start <= begin:
            return out
        current = add_months(current, -span_months)


@dataclass(frozen=True)
class Call:
    view: View
    selected_date: date
    body: dict[str, Any]


@dataclass(frozen=True)
class RunContext:
    property_id: str
    comp_set_id: int
    country_code: str
    process_month: date
    process_week: date
    process_day: date
    daily_begin: date
    monthly_begin: date
    fiscal_year_start_month: int = 1
    currency: str = "USD"

    def user_settings(self) -> dict[str, Any]:
        start = date(self.process_day.year, self.fiscal_year_start_month, 1)
        return {
            "targetCurrency": self.currency,
            "cultureCode": "en-US",
            "currencyConversionMethod": "HistoricalExchangeRate",
            "weekend": ["5", "6"],
            "fiscalYearStartDate": iso(start),
        }


def body_for(view: View, selected: date, ctx: RunContext) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "input": {
            "propertyId": ctx.property_id,
            "userSettings": ctx.user_settings(),
            "propertySetId": ctx.comp_set_id,
            "countryCode": ctx.country_code,
            "selectedDate": iso(selected),
            "currentProcessDate": iso(selected),
            **view.extra,
        },
    }
    if view.operation == "SummaryIndustryTable":
        variables["propertyId"] = ctx.property_id
    return {"operationName": view.operation, "variables": variables, "query": query(view.operation)}


def plan_calls(ctx: RunContext, view_names: list[str] | None = None) -> list[Call]:
    wanted = set(view_names or VIEW_NAMES)
    unknown = wanted - set(VIEW_NAMES)
    if unknown:
        msg = f"Unknown STR views {sorted(unknown)}; choose from {list(VIEW_NAMES)}"
        raise ValueError(msg)
    calls = []
    for view in VIEWS:
        if view.name not in wanted:
            continue
        if view.schedule == "daily":
            dates = daily_dates(ctx.daily_begin, sunday_cutoff(ctx.process_day))
        elif view.schedule == "monthly":
            dates = monthly_dates(ctx.process_month, ctx.monthly_begin, view.span_months)
        else:
            dates = [ctx.process_week]
        calls.extend(Call(view, selected, body_for(view, selected, ctx)) for selected in dates)
    return calls


def bootstrap_body(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
    return {"operationName": operation, "variables": variables, "query": query(operation)}
