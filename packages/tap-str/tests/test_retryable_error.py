"""A CoStar outage is reported to the runner as a retryable error; other failures are not."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from singer_playwright.stream import PlaywrightStream
from tap_str.client import StrStream
from tap_str.community.streams import CoStarUnavailable, GraphQLError


class Stream(StrStream):
    """Just enough of a stream to run get_records: no tap, browser or page."""

    name = "str_series"
    logger = logging.getLogger("tap-str-test")

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def extract_site_records(self, page: Any, context: Any) -> Any:
        raise NotImplementedError


def _events(caplog) -> list[dict]:
    return [json.loads(r.getMessage().split("ARIA ", 1)[1]) for r in caplog.records if "ARIA {" in r.getMessage()]


@pytest.fixture
def failing(monkeypatch):
    def build(exc: Exception) -> Stream:
        def get_records(self, context):
            self.report("error", message=f"{type(exc).__name__}: {exc}")  # as PlaywrightStream does
            raise exc
            yield  # pragma: no cover

        monkeypatch.setattr(PlaywrightStream, "get_records", get_records)
        return Stream(exc)

    return build


def test_a_costar_outage_is_the_last_error_and_retryable(failing, caplog) -> None:
    stream = failing(CoStarUnavailable("CoStar's STR service failed SegDayOfWeekTable 4 times"))
    with caplog.at_level(logging.INFO), pytest.raises(CoStarUnavailable):
        list(stream.get_records(None))
    last = _events(caplog)[-1]
    assert last["kind"] == "error" and last["retryable"] is True
    assert last["code"] == "costar_unavailable" and last["stream"] == "str_series"
    assert last["message"].startswith("CoStar's STR service failed SegDayOfWeekTable")


def test_other_failures_stay_permanent(failing, caplog) -> None:
    with caplog.at_level(logging.INFO), pytest.raises(GraphQLError):
        list(failing(GraphQLError("DailyTable failed (400)")).get_records(None))
    assert [e.get("retryable") for e in _events(caplog)] == [None]


def test_the_runner_reads_it_as_a_transient_failure(failing, caplog) -> None:
    from aria_runner.db import FailureKind
    from aria_runner.progress import Progress, parse
    from aria_runner.resilience import failure_kind
    from aria_runner.sdk import CommandFailed

    with caplog.at_level(logging.INFO), pytest.raises(CoStarUnavailable):
        list(failing(CoStarUnavailable("outage")).get_records(None))
    progress = Progress()
    for record in caplog.records:
        # As the runner sees it: Meltano's line with the tap's message in it.
        evt = parse(f"2026-11-04T12:09:02Z [info     ] tap-str      {record.getMessage()}")
        if evt is not None:
            progress.apply(evt)
    exc = CommandFailed(["meltano"], 1, ["Extractor failed"])
    assert failure_kind(exc, progress) == FailureKind.transient
