"""STR streams: every STAR-tab view, the comp set and its response.

The browser must already be signed in to product.costar.com, by any login path. Every call is a
plain in-page ``fetch`` to the STR GraphQL endpoint, so the page's cookies apply.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from singer_sdk import typing as th

from singer_playwright.login import LoginRejected
from tap_str.client import StrStream
from tap_str.community import parse, plan

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Iterable

    from playwright.sync_api import Page
    from singer_sdk.helpers.types import Context

STR_HOME = "https://product.costar.com/app/str"
BATCH_SIZE = 4

FETCH_JS = """
async ({url, bodies}) => {
  const out = [];
  for (const body of bodies) {
    const resp = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {'content-type': 'application/json', 'accept': 'application/json'},
      body: JSON.stringify(body),
    });
    let json = null;
    try { json = await resp.json(); } catch (e) { json = null; }
    out.push({status: resp.status, json});
  }
  return out;
}
"""


# CoStar sometimes answers 402 SessionExpired about 5 minutes after sign-in although the next
# request works (seen 2026-10-26/27: 3 of ~20 runs, each at 4.9 min into str_series). A batch
# that gets it waits, then is sent again; a session that really ended fails every retry.
RETRY_WAITS_SECONDS = (5, 15)
# A person moving through STAR views takes a few seconds between them. Batches of queries
# wait a random 0.5x-1.5x of query_pause_seconds (config, default 4) between each other.
QUERY_PAUSE_SECONDS = 4.0
LOGIN_HOST = "secure.costargroup.com"
# CoStar's page signs itself out after 2 hours without mouse or keyboard input, however many
# queries it sends (2026-10-28: signed in 11:03:38, sent to /app/signout?r=InactivityTimeout at
# 13:03:45, mid str_series, Hotel Phillips). The session itself lived on: the next hotel in the
# chain reopened STR with it. So a batch that loses its page reopens STR and is sent again, and
# the pauses between batches move the mouse like a person reading the views.
INACTIVITY_SIGNOUT = "InactivityTimeout"
PAGE_GONE = "Execution context was destroyed"


class GraphQLError(RuntimeError):
    pass


def _session_expired(result: dict[str, Any]) -> bool:
    return result.get("status") == 402 or "SessionExpired" in str(result.get("json") or "")


def _tap_of(stream: Any) -> Any:
    return getattr(stream, "_tap", None) if stream is not None else None


def _reopen_str(page: Page, stream: Any, reason: str) -> None:
    """Open STR again on the same session; sign in again (direct logins, once) if it has ended."""
    try:
        open_str(page, stream)
    except LoginRejected:
        tap = _tap_of(stream)
        if tap is None or not hasattr(tap, "sign_in_again") or not tap.sign_in_again(reason):
            raise
        open_str(page, stream)


def _evaluate(page: Page, batch: list[dict[str, Any]], stream: Any = None) -> list[dict[str, Any]]:
    """Send a batch; when the page navigates away mid-batch, reopen STR and send it once more."""
    try:
        return page.evaluate(FETCH_JS, {"url": plan.GRAPHQL_URL, "bodies": batch})
    except Exception as exc:
        if PAGE_GONE not in str(exc):
            raise
    where = page.url
    if INACTIVITY_SIGNOUT in where:
        reason = "CoStar signed the page out for inactivity"
    else:
        reason = f"The page navigated away from STR ({where})"
    if stream is not None:
        stream.screenshot(reason)
        stream.logger.warning("%s; reopening STR and sending the batch again", reason)
    _reopen_str(page, stream, reason)
    results = page.evaluate(FETCH_JS, {"url": plan.GRAPHQL_URL, "bodies": batch})
    if stream is not None:
        stream.notice(f"{reason}; reopening STR worked", code="reopened_str")
    return results


def _fetch(page: Page, batch: list[dict[str, Any]], stream: Any = None) -> list[dict[str, Any]]:
    tap = _tap_of(stream)
    if tap is not None and hasattr(tap, "egress"):
        tap.egress.maybe_record(page)
    results = _evaluate(page, batch, stream)
    expired = [body["operationName"] for body, result in zip(batch, results, strict=True) if _session_expired(result)]
    if not expired:
        return results
    ops = ", ".join(expired)
    if tap is not None and hasattr(tap, "egress"):
        tap.egress.record(page, "at SessionExpired")
    if stream is not None:
        stream.screenshot(f"SessionExpired on {ops}")
    for attempt, wait in enumerate(RETRY_WAITS_SECONDS, start=1):
        if stream is not None:
            stream.logger.warning(
                "CoStar answered SessionExpired on %s; waiting %ss, then retrying (%d of %d)",
                ops, wait, attempt, len(RETRY_WAITS_SECONDS),
            )
        time.sleep(wait)
        results = _evaluate(page, batch, stream)
        if not any(_session_expired(result) for result in results):
            if stream is not None:
                stream.notice(f"CoStar said SessionExpired on {ops}; retry {attempt} worked", code="session_retry_ok")
            return results
    if stream is not None:
        stream.screenshot(f"SessionExpired on {ops} after {len(RETRY_WAITS_SECONDS)} retries")
    # The session really ended (2026-10-27: the dashboard asked for login at sign-out).
    # Sign out what is left and sign in once more, then resend this batch.
    if tap is not None and hasattr(tap, "sign_in_again") and tap.sign_in_again(f"SessionExpired on {ops}"):
        open_str(page, stream)
        results = _evaluate(page, batch, stream)
        if not any(_session_expired(result) for result in results):
            stream.notice(f"Signing in again after SessionExpired on {ops} worked", code="signed_in_again_ok")
        else:
            stream.screenshot(f"SessionExpired on {ops} even after signing in again")
    return results


def move_mouse(page: Page | None) -> None:
    """Drift the mouse to a random spot on the page, as a person does while reading."""
    if page is None:
        return
    try:
        size = page.viewport_size or {"width": 1280, "height": 800}
        x = random.uniform(0.2, 0.8) * size["width"]
        y = random.uniform(0.3, 0.8) * size["height"]
        page.mouse.move(x, y, steps=random.randint(5, 15))
    except Exception:  # noqa: BLE001 - never fail a run over the mouse
        return


def pause(stream: Any = None, page: Page | None = None) -> None:
    """Wait between query batches like a person between views (random, so not a fixed beat).

    With ``page``, the mouse moves partway through, which keeps CoStar's inactivity sign-out away.
    """
    tap = _tap_of(stream)
    config = getattr(tap, "config", None) or {}
    base = float(config.get("query_pause_seconds", QUERY_PAUSE_SECONDS))
    if base > 0:
        wait = random.uniform(0.5 * base, 1.5 * base)
        time.sleep(wait / 2)
        move_mouse(page)
        time.sleep(wait / 2)
    else:
        move_mouse(page)


def graphql(page: Page, bodies: list[dict[str, Any]], *, stream: Any = None) -> list[dict[str, Any]]:
    """POST each body from inside the page; returns each response's ``data``.

    ``stream`` (the calling stream) gets the retry log lines, notices and screenshots.
    """
    out: list[dict[str, Any]] = []
    for start in range(0, len(bodies), BATCH_SIZE):
        batch = bodies[start : start + BATCH_SIZE]
        if start:
            pause(stream, page)
        results = _fetch(page, batch, stream)
        for body, result in zip(batch, results, strict=True):
            payload = result.get("json") or {}
            if result["status"] != 200 or payload.get("errors") or "data" not in payload:
                detail = payload.get("errors") or payload or "no JSON body"
                msg = f"{body['operationName']} failed ({result['status']}): {str(detail)[:300]}"
                raise GraphQLError(msg)
            out.append(payload["data"] or {})
    return out


@dataclass
class Bootstrap:
    ctx: plan.RunContext
    comp_set: dict[str, Any]
    members: dict[str, Any]
    member_details: dict[str, Any]

    @property
    def comp_set_id(self) -> str:
        return str(self.comp_set.get("propertySetId"))

    def member_ids(self) -> list[str]:
        return [str(i) for i in (self.members.get("propertySetMembers") or {}).get("memberPropertyIds") or []]


def open_str(page: Page, stream: Any = None) -> None:
    """Open the STR app once sign-in has settled.

    Right after sign-in CoStar is still redirecting; navigating then is aborted
    (net::ERR_ABORTED, TownePlace Phoenix Glendale 2026-10-27). Wait for the page to load,
    retry an aborted load once, and say plainly when CoStar sends us back to its login page.
    """
    for attempt in (1, 2):
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except Exception:  # noqa: BLE001 - a slow page still gets the goto below
            pass
        if page.url.startswith(STR_HOME):
            return
        try:
            page.goto(STR_HOME, wait_until="domcontentloaded", timeout=90_000)
        except Exception as exc:
            if "ERR_ABORTED" not in str(exc) or attempt == 2:
                if stream is not None:
                    stream.screenshot("Opening STR failed")
                raise
            if stream is not None:
                stream.screenshot("Opening STR was aborted after sign-in")
                stream.logger.warning("Opening STR was aborted (%s); waiting 5s, then retrying", str(exc).splitlines()[0])
            time.sleep(5)
            continue
        if LOGIN_HOST in page.url:
            if stream is not None:
                stream.screenshot("CoStar returned to its login page")
            msg = "CoStar returned to its login page after sign-in"
            raise LoginRejected(msg)
        return


def bootstrap(page: Page, config: dict[str, Any], *, stream: Any = None) -> Bootstrap:
    """Process dates, comp set and members: everything the data calls need."""
    property_id = str(config["property_id"])
    open_str(page, stream)
    tap = _tap_of(stream)
    if tap is not None and hasattr(tap, "egress"):
        tap.egress.record(page, "at sign-in")
    today = datetime.now(UTC).date()
    dashboard, detail = graphql(
        page,
        [
            plan.bootstrap_body(
                "DashboardPropertyList",
                {
                    "input": {
                        "propertyIds": [int(property_id)],
                        "pageNumber": 1,
                        "pageSize": 10,
                        "sortColumn": "hotelName",
                        "sortDirection": "Ascending",
                    },
                },
            ),
            plan.bootstrap_body(
                "PropertyDetailV2",
                {"filter": property_id, "propertyIds": [int(property_id)], "currentProcessDate": plan.iso(today)},
            ),
        ],
        stream=stream,
    )
    dates = parse.process_dates(dashboard)
    comp_set = parse.pick_comp_set(detail, config.get("comp_set_id"))
    members, = graphql(
        page, [plan.bootstrap_body("PropertySetMembers", {"propertySetId": str(comp_set["propertySetId"])})], stream=stream
    )
    ids = [*[str(i) for i in (members.get("propertySetMembers") or {}).get("memberPropertyIds") or []], property_id]
    details, = graphql(
        page,
        [plan.bootstrap_body("PropertyDetailListV2", {"filter": ids, "currentProcessDate": plan.iso(today)})],
        stream=stream,
    )
    ctx = plan.RunContext(
        property_id=property_id,
        comp_set_id=int(comp_set["propertySetId"]),
        country_code=parse.country_code(dashboard),
        process_month=date.fromisoformat(dates["month"]),
        process_week=date.fromisoformat(dates["week"]),
        process_day=date.fromisoformat(dates["day"]),
        daily_begin=date.fromisoformat(str(config.get("begin_date") or "2024-01-01")[:10]),
        monthly_begin=date.fromisoformat(str(config.get("monthly_begin_date") or "2024-01-01")[:10]),
        fiscal_year_start_month=int(config.get("fiscal_year_start_month") or 1),
    )
    return Bootstrap(ctx, comp_set, members, details)


class CommunityStream(StrStream):
    """Base for STR streams: one bootstrap per run, shared by every stream."""

    def _bootstrap(self, page: Page) -> Bootstrap:
        tap = self._tap
        cached = getattr(tap, "_str_community_bootstrap", None)
        if cached is None:
            cached = bootstrap(page, dict(self.config), stream=self)
            tap._str_community_bootstrap = cached  # noqa: SLF001 - shared across streams in one run
        return cached

    @override
    def extract_site_records(self, page: Page, context: Context | None) -> Iterable[dict]:
        yield from self.community_records(page, self._bootstrap(page))

    def community_records(self, page: Page, boot: Bootstrap) -> Iterable[dict]:
        raise NotImplementedError


class SeriesStream(CommunityStream):
    """Every value from every STAR-tab view, one row each, raw identifiers kept."""

    name = "str_series"
    primary_keys = (
        "property_id",
        "comp_set_id",
        "source_view",
        "selected_date",
        "block",
        "slice_type",
        "calc_type",
        "key",
    )
    replication_key = None
    schema = th.PropertiesList(
        th.Property("property_id", th.StringType, required=True),
        th.Property("comp_set_id", th.StringType, required=True),
        th.Property("source_view", th.StringType, required=True, description="Tap view name, e.g. seg_comp_set"),
        th.Property("operation", th.StringType, description="STR GraphQL operation"),
        th.Property("block", th.StringType, required=True, description="Path of the block in the response"),
        th.Property("mode", th.StringType, description="industry or comp-set when the view has both"),
        th.Property("selected_date", th.DateType, required=True),
        th.Property("key", th.StringType, required=True, description="Raw STR timeIndex key"),
        th.Property("key_date", th.DateType, description="Decoded key: the day, or the month's last day"),
        th.Property("key_grain", th.StringType, description="day or month"),
        th.Property("slice_type", th.StringType, required=True),
        th.Property("label", th.StringType),
        th.Property("entity", th.StringType),
        th.Property("segment", th.StringType),
        th.Property("calc_type", th.StringType, required=True),
        th.Property("metric", th.StringType),
        th.Property("measure", th.StringType),
        th.Property("window", th.StringType),
        th.Property("weekday", th.StringType),
        th.Property("value", th.NumberType),
        th.Property("value_text", th.StringType),
        th.Property("data_as_of", th.DateTimeType),
        th.Property("extracted_at", th.DateTimeType),
    ).to_dict()

    @override
    def community_records(self, page: Page, boot: Bootstrap) -> Iterable[dict]:
        calls = plan.plan_calls(boot.ctx, self.config.get("community_views"))
        self.logger.info(
            "STR community: %s calls for property %s comp set %s",
            len(calls),
            boot.ctx.property_id,
            boot.comp_set_id,
        )
        extracted_at = datetime.now(UTC).isoformat()
        for start in range(0, len(calls), BATCH_SIZE):
            batch = calls[start : start + BATCH_SIZE]
            if start:
                pause(self, page)
            for call, data in zip(batch, graphql(page, [c.body for c in batch], stream=self), strict=True):
                for row in parse.series_rows(
                    data,
                    view=call.view.name,
                    key_grain=call.view.key_grain,
                    selected_date=call.selected_date.isoformat(),
                    mode=call.view.mode,
                ):
                    yield {
                        "property_id": boot.ctx.property_id,
                        "comp_set_id": boot.comp_set_id,
                        "operation": call.view.operation,
                        **row,
                        "extracted_at": extracted_at,
                    }
            self.polite_sleep()


class CompSetMembersStream(CommunityStream):
    """Who is in the comp set, with each hotel's details."""

    name = "comp_set_members"
    primary_keys = ("comp_set_id", "property_id")
    replication_key = None
    schema = th.PropertiesList(
        th.Property("comp_set_id", th.StringType, required=True),
        th.Property("comp_set_name", th.StringType),
        th.Property("comp_set_type", th.StringType),
        th.Property("property_id", th.StringType, required=True),
        th.Property("is_subject", th.BooleanType),
        th.Property("str_id", th.StringType),
        th.Property("hotel_name", th.StringType),
        th.Property("brand_name", th.StringType),
        th.Property("parent_company", th.StringType),
        th.Property("owners", th.StringType),
        th.Property("operators", th.StringType),
        th.Property("num_rooms", th.IntegerType),
        th.Property("hotel_class", th.StringType),
        th.Property("market", th.StringType),
        th.Property("submarket", th.StringType),
        th.Property("address", th.StringType),
        th.Property("city", th.StringType),
        th.Property("state", th.StringType),
        th.Property("postal_code", th.StringType),
        th.Property("country_code", th.StringType),
        th.Property("latitude", th.NumberType),
        th.Property("longitude", th.NumberType),
        th.Property("open_date", th.StringType),
        th.Property("operating_status", th.StringType),
    ).to_dict()

    @override
    def community_records(self, page: Page, boot: Bootstrap) -> Iterable[dict]:
        yield from parse.member_rows(
            boot.members,
            boot.member_details,
            comp_set=boot.comp_set,
            subject_property_id=boot.ctx.property_id,
        )


class CompSetResponseStream(CommunityStream):
    """Which comp set hotels submitted data each month (STR's Response view)."""

    name = "comp_set_response"
    primary_keys = ("comp_set_id", "property_id", "series_name", "period_date")
    replication_key = None
    schema = th.PropertiesList(
        th.Property("comp_set_id", th.StringType, required=True),
        th.Property("property_id", th.StringType, required=True),
        th.Property("census_id", th.StringType),
        th.Property("series_name", th.StringType, required=True),
        th.Property("period_date", th.DateType, required=True),
        th.Property("numeric_value", th.NumberType),
        th.Property("string_value", th.StringType),
    ).to_dict()

    @override
    def community_records(self, page: Page, boot: Bootstrap) -> Iterable[dict]:
        month = boot.ctx.process_month
        ids = [int(i) for i in [*boot.member_ids(), boot.ctx.property_id]]
        data, = graphql(
            page,
            [
                plan.bootstrap_body(
                    "ResponseTableScalar",
                    {
                        "input": {
                            "propertyIds": ids,
                            "selectedDate": plan.iso(month),
                            "dataModelType": "MonthlySubmission",
                            "compareType": "DateToDate",
                            "interval": "Monthly",
                        },
                        "roomCountDate": plan.iso(month.replace(day=1)),
                    },
                ),
            ],
            stream=self,
        )
        yield from parse.response_rows(data, comp_set_id=boot.comp_set_id)
