"""Community (brand portal) parsing and call planning. Values are made up."""

from datetime import date

import pytest

from tap_str.community import parse, plan


@pytest.mark.parametrize(
    ("calc", "expected"),
    [
        ("HotelOccupancy", ("occupancy", "value", "period", None)),
        ("AverageDailyRate", ("adr", "value", "period", None)),
        ("RevenuePerAvailableRoomRank", ("revpar", "rank", "period", None)),
        ("HotelADRGrowthYoy", ("adr", "change_yoy", "period", None)),
        ("HotelADRGrowthYoyRank", ("adr", "rank_change_yoy", "period", None)),
        ("HotelThreeMonthADRFridayChange", ("adr", "change_yoy", "3mo", "friday")),
        ("HotelTwelvemonthRevPARWeekendAverage", ("revpar", "value", "12mo", "weekend")),
        ("HotelYTDMonthSupplyChange", ("supply", "change_yoy", "ytd", None)),
        ("HotelMTDMonthOccupancyAverage", ("occupancy", "value", "mtd", None)),
        ("HotelMTDWeekdayRevPARYoyRank", ("revpar", "rank_change_yoy", "mtd", "weekday")),
        ("HotelSevenDayADRYoy", ("adr", "change_yoy", "7d", None)),
        ("HotelTwentyEightDayPercentOfTotalRevenue", ("pct_of_total_revenue", "value", "28d", None)),
        ("PercentOfTotalRevenueLast4Monday", ("pct_of_total_revenue", "value", "last4", "monday")),
        ("HotelOccupancyLast4SundayGrowthYoy", ("occupancy", "change_yoy", "last4", "sunday")),
        ("HotelOpenRoomCount", ("open_room_count", "value", "period", None)),
        ("HotelSamplePropertyCount", ("sample_property_count", "value", "period", None)),
        ("HotelTwelvemonthRankGrowthYoyPropertyCount", (None, "rank_property_count_yoy", "12mo", None)),
        ("RankPropertyCount", (None, "rank_property_count", "period", None)),
        ("GrowthYoyFridayThreeMonthSufficiencyAndIsolationChecks", (None, "check", "3mo", "friday")),
        ("PassBrandCheck", (None, "check", "period", None)),
        ("HotelTDMonthWeekendChangeCompleteSufficiency", (None, "check", None, "weekend")),
    ],
)
def test_parse_calc(calc: str, expected: tuple) -> None:
    got = parse.parse_calc(calc)
    assert (got["metric"], got["measure"], got["window"], got["weekday"]) == expected


@pytest.mark.parametrize(
    ("slice_type", "expected"),
    [
        ("SubjectProperty", ("subject_property", None)),
        ("CompSetTransient", ("comp_set", "transient")),
        ("IndexGroup", ("index", "group")),
        ("IndustryFoodBeverage", ("industry", "food_beverage")),
        ("MarketClass", ("market_class", None)),
        ("SubmarketClass", ("submarket_class", None)),
        ("Submarket", ("submarket", None)),
        ("SubjectPropertyRooms", ("subject_property", "rooms")),
        ("SomethingNew", (None, None)),
    ],
)
def test_parse_slice(slice_type: str, expected: tuple) -> None:
    assert parse.parse_slice(slice_type) == expected


SEG_RESPONSE = {
    "SegTableData": {
        "compSet": {
            "timeIndex": [2026230831, 2026230930],
            "lastUpdatedDate": "2026-10-21T06:05:06+00:00",
            "data": [
                {
                    "benchmarkingDataSliceType": "CompSetTransient",
                    "dataCalculationType": "HotelOccupancy",
                    "values": [0.5, None],
                    "label": "CompSet",
                },
                {
                    "benchmarkingDataSliceType": "IndexGroup",
                    "dataCalculationType": "HotelYTDMonthADRChange",
                    "values": [0.01, "n/a"],
                    "label": "Index",
                },
            ],
        },
    },
}


def test_series_rows_keep_raw_ids_and_skip_nulls() -> None:
    rows = list(
        parse.series_rows(SEG_RESPONSE, view="seg_comp_set", key_grain="month", selected_date="2026-09-30", mode="comp-set")
    )
    assert len(rows) == 3
    first = rows[0]
    assert first["block"] == "SegTableData.compSet"
    assert (first["key"], first["key_date"]) == ("2026230831", "2026-08-31")
    assert (first["entity"], first["segment"], first["metric"], first["measure"]) == (
        "comp_set",
        "transient",
        "occupancy",
        "value",
    )
    assert first["data_as_of"] == "2026-10-21T06:05:06+00:00"
    assert rows[1]["window"] == "ytd"
    assert (rows[2]["value"], rows[2]["value_text"]) == (None, "n/a")


def test_series_and_response_rows_match_stream_schemas() -> None:
    import jsonschema

    from tap_str.community.streams import CompSetResponseStream, SeriesStream

    extra = {"property_id": "99", "comp_set_id": "2", "operation": "SegIndustryTable", "extracted_at": "2026-10-21T00:00:00Z"}
    for row in parse.series_rows(SEG_RESPONSE, view="seg_comp_set", key_grain="month", selected_date="2026-09-30"):
        jsonschema.validate({**extra, **row}, SeriesStream.schema)
    response = {
        "ResponseTableScalar": {
            "submission": [
                {"censusID": 5, "propertyID": 11, "seriesData": [{"seriesName": "DailySubmitted", "value": [{"timeIndex": 20260930, "numericValue": "1", "stringValue": 7}]}]},
            ],
        },
    }
    for row in parse.response_rows(response, comp_set_id="2"):
        jsonschema.validate(row, CompSetResponseStream.schema)


def test_bootstrap_helpers() -> None:
    dashboard = {
        "DashboardPropertyList": {
            "currentProcessDates": {
                "currentProcessMonth": "2026-09-30T00:00:00+00:00",
                "currentProcessWeek": "2026-10-17T00:00:00+00:00",
                "currentProcessDay": "2026-10-20T00:00:00+00:00",
            },
            "propertyDetails": [{"countryCode": "USA"}],
        },
    }
    assert parse.process_dates(dashboard) == {"month": "2026-09-30", "week": "2026-10-17", "day": "2026-10-20"}
    assert parse.country_code(dashboard) == "USA"
    with pytest.raises(ValueError, match="missing process dates"):
        parse.process_dates({"DashboardPropertyList": {"currentProcessDates": {}}})

    detail = {
        "compsetList": [
            {"propertySetId": 1, "name": "Custom", "isCompSetPerformance": False},
            {"propertySetId": 2, "name": "Performance Set", "isCompSetPerformance": True},
        ],
    }
    assert parse.pick_comp_set(detail)["propertySetId"] == 2
    assert parse.pick_comp_set(detail, "1")["name"] == "Custom"
    with pytest.raises(ValueError, match="not visible"):
        parse.pick_comp_set(detail, "9")


def test_member_rows_include_subject() -> None:
    members = {"propertySetMembers": {"propertySetId": "2", "memberPropertyIds": ["11", "12"]}}
    details = {
        "PropertyDetailListV2": {
            "property": [
                {"id": "11", "hotelName": "Hotel A", "numRooms": 120, "owners": [{"name": "Owner Co"}]},
                {"id": "12", "hotelName": "Hotel B", "parentCompany": {"name": "Parent"}},
                {"id": "99", "hotelName": "Subject"},
            ],
        },
    }
    rows = list(parse.member_rows(members, details, comp_set={"propertySetId": 2, "name": "Perf"}, subject_property_id="99"))
    assert [(r["property_id"], r["is_subject"]) for r in rows] == [("11", False), ("12", False), ("99", True)]
    assert rows[0]["owners"] == "Owner Co"
    assert rows[1]["parent_company"] == "Parent"


def test_member_rows_coerce_mixed_types_to_the_schema() -> None:
    import jsonschema

    from tap_str.community.streams import CompSetMembersStream

    details = {
        "PropertyDetailListV2": {
            "property": [
                {
                    "id": "11",
                    "hotelClass": 3,
                    "postalCode": 75220,
                    "numRooms": "248",
                    "latitude": "32.86",
                    "strId": 23860,
                    "openDate": 1998,
                },
            ],
        },
    }
    members = {"propertySetMembers": {"memberPropertyIds": ["11"]}}
    rows = list(parse.member_rows(members, details, comp_set={"propertySetId": 2}, subject_property_id="99"))
    row = rows[0]
    assert (row["hotel_class"], row["postal_code"], row["num_rooms"], row["latitude"]) == ("3", "75220", 248, 32.86)
    for record in rows:
        jsonschema.validate(record, CompSetMembersStream.schema)


def test_response_rows() -> None:
    data = {
        "ResponseTableScalar": {
            "submission": [
                {
                    "censusID": 5,
                    "propertyID": "11",
                    "seriesData": [
                        {"seriesName": "MonthlySubmitted", "value": [{"timeIndex": 20260930, "numericValue": 1}]},
                    ],
                },
            ],
        },
    }
    assert list(parse.response_rows(data, comp_set_id="2")) == [
        {
            "comp_set_id": "2",
            "property_id": "11",
            "census_id": "5",
            "series_name": "MonthlySubmitted",
            "period_date": "2026-09-30",
            "numeric_value": 1,
            "string_value": None,
        },
    ]


@pytest.mark.parametrize(
    ("process_day", "cutoff"),
    [
        (date(2026, 10, 20), date(2026, 10, 18)),  # Tuesday release
        (date(2026, 10, 21), date(2026, 10, 18)),
        (date(2026, 10, 19), date(2026, 10, 18)),  # Monday
        (date(2026, 10, 18), date(2026, 10, 11)),  # Sunday itself is not complete yet
    ],
)
def test_sunday_cutoff(process_day: date, cutoff: date) -> None:
    assert plan.sunday_cutoff(process_day) == cutoff


def test_daily_dates_one_per_month_ending_at_cutoff() -> None:
    assert plan.daily_dates(date(2026, 8, 15), date(2026, 10, 18)) == [
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 18),
    ]
    assert plan.daily_dates(date(2026, 11, 1), date(2026, 10, 18)) == []


def test_monthly_dates_step_back_without_overlap() -> None:
    assert plan.monthly_dates(date(2026, 9, 30), date(2024, 1, 1), 26) == [date(2026, 9, 30), date(2024, 7, 31)]
    assert plan.monthly_dates(date(2026, 9, 30), date(2026, 7, 1), 1) == [
        date(2026, 9, 30),
        date(2026, 8, 31),
        date(2026, 7, 31),
    ]


CTX = plan.RunContext(
    property_id="99",
    comp_set_id=2,
    country_code="USA",
    process_month=date(2026, 9, 30),
    process_week=date(2026, 10, 17),
    process_day=date(2026, 10, 20),
    daily_begin=date(2026, 9, 1),
    monthly_begin=date(2026, 7, 1),
)


def test_plan_calls_and_bodies() -> None:
    calls = plan.plan_calls(CTX)
    by_view: dict[str, list] = {}
    for call in calls:
        by_view.setdefault(call.view.name, []).append(call)
    assert set(by_view) == set(plan.VIEW_NAMES)
    assert [c.selected_date for c in by_view["daily"]] == [date(2026, 9, 30), date(2026, 10, 18)]
    assert [c.selected_date for c in by_view["seg_day_of_week_comp_set"]] == [
        date(2026, 9, 30),
        date(2026, 8, 31),
        date(2026, 7, 31),
    ]
    assert [c.selected_date for c in by_view["seg_weekly"]] == [date(2026, 10, 17)]

    body = by_view["seg_comp_set"][0].body
    assert body["operationName"] == "SegIndustryTable"
    assert "query SegIndustryTable" in body["query"]
    assert body["variables"]["input"]["propertyDetailMode"] == "comp-set"
    assert body["variables"]["input"]["propertySetId"] == 2
    assert body["variables"]["input"]["selectedDate"] == "2026-09-30T00:00:00.000Z"
    assert by_view["summary"][0].body["variables"]["propertyId"] == "99"


def test_plan_calls_rejects_unknown_views() -> None:
    assert {c.view.name for c in plan.plan_calls(CTX, ["daily"])} == {"daily"}
    with pytest.raises(ValueError, match="Unknown STR views"):
        plan.plan_calls(CTX, ["trend"])


def test_every_view_has_a_query() -> None:
    for operation in {v.operation for v in plan.VIEWS} | {
        "DashboardPropertyList",
        "PropertyDetailV2",
        "PropertySetMembers",
        "PropertyDetailListV2",
        "ResponseTableScalar",
    }:
        assert f"query {operation}" in plan.query(operation)


def test_decode_date_key() -> None:
    assert parse.decode_date_key("2026241009") == "2026-10-09"
    assert parse.decode_date_key("2026230920") == "2026-09-20"
    assert parse.decode_date_key("2025230921") == "2025-09-21"
    assert parse.decode_date_key("nope") is None


def test_decode_date_key_every_month() -> None:
    # Keys copied from a SummaryIndustryTable response (Aug 2024 - Sep 2026).
    keys = {
        "2024230831": "2024-08-31",
        "2024241130": "2024-11-30",
        "2024241231": "2024-12-31",
        "2025110131": "2025-01-31",
        "2025110228": "2025-02-28",
        "2025110331": "2025-03-31",
        "2025120430": "2025-04-30",
        "2025120630": "2025-06-30",
        "2025230731": "2025-07-31",
    }
    for key, expected in keys.items():
        assert parse.decode_date_key(key) == expected
    # Quarter or half that disagrees with the month is not a date key.
    assert parse.decode_date_key("2025240831") is None
    assert parse.decode_date_key("2025130131") is None
