"""Str streams."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from singer_sdk import typing as th

from tap_str.client import StrStream
from tap_str.daily import KEEP_OPERATIONS, records_from_calls, shift_date

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Iterable

    from playwright.sync_api import Page, Request
    from singer_sdk.helpers.types import Context

DAILY_URL = "https://product.costar.com/app/str/detail/{property_id}/data/day"
GRAPHQL_URL = "https://product.costar.com/app/str-mfe/api/graphql"
GRAPHQL_PATH = "/app/str-mfe/api/graphql"


class DailyStream(StrStream):
    """One row per stay date from the STR daily grid.

    Opens the daily page so the browser is signed in, copies the
    PortfolioListingAll requests the page just sent, and fetches them again
    with the configured dates.
    """

    name = "daily"
    primary_keys = ("property_id", "stay_date", "window")
    replication_key = None
    schema = th.PropertiesList(
        th.Property("property_id", th.StringType, required=True),
        th.Property("stay_date", th.DateType, required=True),
        th.Property("window", th.StringType, required=True),
        th.Property("begin_date", th.DateType),
        th.Property("end_date", th.DateType),
        th.Property("hotel_name", th.StringType),
        th.Property("str_id", th.StringType),
        th.Property("num_rooms", th.IntegerType),
        th.Property("brand_name", th.StringType),
        th.Property("market", th.StringType),
        th.Property("submarket", th.StringType),
        th.Property("address", th.StringType),
        th.Property("owner_name", th.StringType),
        th.Property("operator_name", th.StringType),
        th.Property("comp_set_id", th.StringType),
        th.Property("comp_set_name", th.StringType),
        th.Property("comp_set_property_count", th.IntegerType),
        th.Property("comp_set_room_count", th.IntegerType),
        th.Property("supply", th.NumberType),
        th.Property("demand", th.NumberType),
        th.Property("revenue", th.NumberType),
        th.Property("occupancy", th.NumberType),
        th.Property("adr", th.NumberType),
        th.Property("revpar", th.NumberType),
        th.Property("comp_set_supply", th.NumberType),
        th.Property("comp_set_demand", th.NumberType),
        th.Property("comp_set_revenue", th.NumberType),
        th.Property("comp_set_occupancy", th.NumberType),
        th.Property("comp_set_adr", th.NumberType),
        th.Property("comp_set_revpar", th.NumberType),
        th.Property("occupancy_index", th.NumberType),
        th.Property("adr_index", th.NumberType),
        th.Property("revpar_index", th.NumberType),
    ).to_dict()

    @override
    def extract_site_records(
        self,
        page: Page,
        context: Context | None,
    ) -> Iterable[dict]:
        property_id = str(self.config["property_id"])
        begin_date = str(self.config["begin_date"])
        end_date = str(self.config["end_date"])
        calls = _load_page_calls(page, property_id)
        templates = [call["payload"] for call in calls if call.get("payload")]
        fetched = _fetch_listing(page, templates, begin_date, end_date)
        if self.config.get("include_last_year", True):
            fetched.extend(
                _fetch_listing(
                    page,
                    templates,
                    shift_date(begin_date, -364),
                    shift_date(end_date, -364),
                ),
            )
        details = [call for call in calls if call["operation"] != "PortfolioListingAll"]
        yield from records_from_calls([*details, *fetched])


def _load_page_calls(page: Page, property_id: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def on_finished(request: Request) -> None:
        try:
            if request.resource_type not in {"xhr", "fetch"}:
                return
            if GRAPHQL_PATH not in request.url:
                return
            response = request.response()
            if response is None or response.status != 200:
                return
            post = request.post_data
            if not post:
                return
            payload = json.loads(post)
            operation = payload.get("operationName")
            if operation not in KEEP_OPERATIONS:
                return
            listing_count = sum(call["operation"] == "PortfolioListingAll" for call in calls)
            if operation == "PortfolioListingAll" and listing_count >= 3:
                return
            body = response.json()
            calls.append(
                {
                    "operation": operation,
                    "variables": payload.get("variables") or {},
                    "data": body.get("data") or {},
                    "payload": payload if operation == "PortfolioListingAll" else None,
                },
            )
        except Exception:  # noqa: BLE001 - capture must not break the page
            return

    page.context.on("requestfinished", on_finished)
    try:
        for _ in range(3):
            try:
                page.goto(
                    DAILY_URL.format(property_id=property_id),
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
                break
            except Exception as exc:
                if "ERR_ABORTED" not in str(exc):
                    raise
                page.wait_for_timeout(2000)
        _wait_for_listings(page, calls, needed=3)
    finally:
        page.context.remove_listener("requestfinished", on_finished)
    if sum(call["operation"] == "PortfolioListingAll" for call in calls) < 3:
        msg = "Daily page did not send the PortfolioListingAll requests"
        raise RuntimeError(msg)
    return calls


def _fetch_listing(
    page: Page,
    templates: list[dict[str, Any]],
    begin_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    bodies = []
    for template in templates:
        body = json.loads(json.dumps(template))
        body["variables"]["input"]["beginDate"] = f"{begin_date[:10]}T00:00:00.000Z"
        body["variables"]["input"]["endDate"] = f"{end_date[:10]}T00:00:00.000Z"
        bodies.append(body)
    page.set_default_timeout(120_000)
    results = page.evaluate(
        """
        async ({url, bodies}) => {
          const out = [];
          for (const body of bodies) {
            const resp = await fetch(url, {
              method: 'POST',
              credentials: 'include',
              headers: {'content-type': 'application/json', 'accept': 'application/json'},
              body: JSON.stringify(body),
            });
            out.push({status: resp.status, json: await resp.json(), body});
          }
          return out;
        }
        """,
        {"url": GRAPHQL_URL, "bodies": bodies},
    )
    calls = []
    for result in results:
        if result["status"] != 200 or (result.get("json") or {}).get("error"):
            msg = f"PortfolioListingAll failed with status {result['status']}"
            raise RuntimeError(msg)
        calls.append(
            {
                "operation": "PortfolioListingAll",
                "variables": result["body"]["variables"],
                "data": (result.get("json") or {}).get("data") or {},
            },
        )
    return calls


def _wait_for_listings(page: Page, calls: list[dict[str, Any]], *, needed: int) -> None:
    for _ in range(80):
        if sum(call["operation"] == "PortfolioListingAll" for call in calls) >= needed:
            return
        page.wait_for_timeout(250)
