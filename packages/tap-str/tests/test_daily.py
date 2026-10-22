"""Daily grid parsing."""

from tap_str.daily import decode_date_key, records_from_calls, shift_date


def test_last_year_shift_matches_the_page() -> None:
    assert shift_date("2026-09-20", -364) == "2025-09-21"
    assert shift_date("2026-10-09", -364) == "2025-10-10"


def test_decode_date_key() -> None:
    assert decode_date_key("2026241009") == "2026-10-09"
    assert decode_date_key("2026230920") == "2026-09-20"
    assert decode_date_key("2025230921") == "2025-09-21"
    assert decode_date_key("nope") is None


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
        assert decode_date_key(key) == expected
    # Quarter or half that disagrees with the month is not a date key.
    assert decode_date_key("2025240831") is None
    assert decode_date_key("2025130131") is None


def test_records_join_windows_and_series() -> None:
    calls = [
        {
            "operation": "PropertyDetailV2",
            "variables": {},
            "data": {
                "propertyDetailV2": {
                    "property": {
                        "hotelName": "Hilton Garden Inn Boston-Burlington",
                        "numRooms": 181,
                        "owners": [{"name": "Arbor Lodging Partners"}],
                    },
                    "SecurePropertyContext": {"id": 4603810, "strId": 1421},
                },
                "compsetList": [{"propertySetId": "1391861", "name": "Comp Set", "propertyCount": 7, "roomCount": 1397}],
            },
        },
        {
            "operation": "PortfolioListingAll",
            "variables": {
                "input": {
                    "beginDate": "2026-09-20T00:00:00.000Z",
                    "endDate": "2026-10-09T00:00:00.000Z",
                    "idType": "Property",
                    "ids": [4603810],
                    "dataTypes": ["SubjectProperty"],
                },
            },
            "data": {
                "PortfolioListingAll": {
                    "data": {"occupancy": {"2026230920": {"dataPt": 0.45, "dataFormatType": "percent"}}},
                },
            },
        },
        {
            "operation": "PortfolioListingAll",
            "variables": {
                "input": {
                    "beginDate": "2026-09-20T00:00:00.000Z",
                    "endDate": "2026-10-09T00:00:00.000Z",
                    "idType": "CompSet",
                    "ids": [1391861],
                    "dataTypes": ["CompSet"],
                },
            },
            "data": {
                "PortfolioListingAll": {
                    "data": {"compSetOccupancy": {"2026230920": {"dataPt": 0.59}}},
                },
            },
        },
        {
            "operation": "PortfolioListingAll",
            "variables": {
                "input": {
                    "beginDate": "2025-09-21T00:00:00.000Z",
                    "endDate": "2025-10-10T00:00:00.000Z",
                    "idType": "Property",
                    "ids": [4603810],
                    "dataTypes": ["SubjectProperty"],
                },
            },
            "data": {
                "PortfolioListingAll": {
                    "data": {"occupancy": {"2025230921": {"dataPt": 0.5}}},
                },
            },
        },
    ]

    rows = records_from_calls(calls)

    assert [(row["stay_date"], row["window"], row["occupancy"]) for row in rows] == [
        ("2026-09-20", "current", 0.45),
        ("2025-09-21", "last_year", 0.5),
    ]
    current = rows[0]
    assert current["property_id"] == "4603810"
    assert current["str_id"] == "1421"
    assert current["comp_set_id"] == "1391861"
    assert current["comp_set_occupancy"] == 0.59
    assert current["hotel_name"] == "Hilton Garden Inn Boston-Burlington"
    assert current["begin_date"] == "2026-09-20"
    assert current["end_date"] == "2026-10-09"
