"""SessionExpired is retried after a wait; opening STR waits out the sign-in redirects."""

from __future__ import annotations

import logging

import pytest

from singer_playwright.login import LoginRejected
from tap_str.community import streams

OK = {"status": 200, "json": {"data": {"x": 1}}}
EXPIRED = {
    "status": 402,
    "json": {"error": "Authentication required", "reason": "SessionExpired"},
}
BODIES = [{"operationName": "SegDayOfWeekTable"}, {"operationName": "DailyTable"}]


class FakePage:
    def __init__(self, answers: list[list[dict]]) -> None:
        self.answers = answers
        self.calls = 0

    def evaluate(self, _js: str, args: dict) -> list[dict]:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return [answer[i % len(answer)] for i in range(len(args["bodies"]))]


class FakeStream:
    def __init__(self) -> None:
        self.logger = logging.getLogger("tap-str-test")
        self.screens: list[str] = []
        self.notices: list[tuple[str, str | None]] = []

    def screenshot(self, label: str) -> None:
        self.screens.append(label)

    def notice(self, message: str, *, code: str | None = None, warning: bool = False) -> None:
        del warning
        self.notices.append((message, code))


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(streams.time, "sleep", slept.append)
    return slept


def test_a_session_expired_batch_waits_then_retries_and_carries_on(waits, caplog) -> None:
    page, stream = FakePage([[OK, EXPIRED], [OK]]), FakeStream()
    with caplog.at_level(logging.WARNING):
        data = streams.graphql(page, BODIES, stream=stream)
    assert data == [{"x": 1}, {"x": 1}]
    assert waits == [5]  # waited before retrying, not immediately
    assert page.calls == 2
    assert stream.screens == ["SessionExpired on DailyTable"]
    assert stream.notices[0][1] == "session_retry_ok"
    assert "waiting 5s, then retrying (1 of 2)" in caplog.text


def test_a_session_that_really_ended_fails_after_every_retry(waits) -> None:
    page, stream = FakePage([[EXPIRED]]), FakeStream()
    with pytest.raises(streams.GraphQLError, match="402.*SessionExpired"):
        streams.graphql(page, BODIES, stream=stream)
    assert waits == list(streams.RETRY_WAITS_SECONDS)
    assert page.calls == 1 + len(streams.RETRY_WAITS_SECONDS)
    assert stream.screens == [
        "SessionExpired on SegDayOfWeekTable, DailyTable",
        "SessionExpired on SegDayOfWeekTable, DailyTable after 2 retries",
    ]


def test_other_errors_are_not_retried(waits) -> None:
    page = FakePage([[{"status": 500, "json": {"errors": ["boom"]}}]])
    with pytest.raises(streams.GraphQLError, match="500"):
        streams.graphql(page, BODIES[:1], stream=FakeStream())
    assert waits == [] and page.calls == 1


class NavPage:
    """A tab that is still redirecting after sign-in: the first goto is aborted."""

    def __init__(self, *, aborts: int = 1, lands_on: str = streams.STR_HOME + "/dashboard") -> None:
        self.url = "https://product.costar.com/suiteapps/home"
        self.aborts = aborts
        self.lands_on = lands_on
        self.gotos = 0

    def wait_for_load_state(self, *_a, **_kw) -> None:
        return None

    def goto(self, _url: str, **_kw) -> None:
        self.gotos += 1
        if self.gotos <= self.aborts:
            raise RuntimeError("Page.goto: net::ERR_ABORTED at https://product.costar.com/app/str")
        self.url = self.lands_on


def test_an_aborted_str_load_after_sign_in_is_retried_once(waits) -> None:
    page, stream = NavPage(aborts=1), FakeStream()
    streams.open_str(page, stream)
    assert page.gotos == 2 and waits == [5]
    assert stream.screens == ["Opening STR was aborted after sign-in"]


def test_two_aborted_loads_fail_with_a_screenshot(waits) -> None:
    page, stream = NavPage(aborts=2), FakeStream()
    with pytest.raises(RuntimeError, match="ERR_ABORTED"):
        streams.open_str(page, stream)
    assert stream.screens[-1] == "Opening STR failed"


def test_being_sent_back_to_the_login_page_says_so(waits) -> None:
    page, stream = NavPage(aborts=0, lands_on="https://secure.costargroup.com/login?signin=x"), FakeStream()
    with pytest.raises(LoginRejected, match="returned to its login page"):
        streams.open_str(page, stream)
    assert stream.screens == ["CoStar returned to its login page"]


def test_already_on_str_opens_nothing(waits) -> None:
    page = NavPage()
    page.url = streams.STR_HOME + "/dashboard"
    streams.open_str(page, FakeStream())
    assert page.gotos == 0 and waits == []


class FakeEgress:
    def __init__(self) -> None:
        self.moments: list[str] = []

    def maybe_record(self, _page) -> None:  # noqa: ANN001
        self.moments.append("check")

    def record(self, _page, moment: str) -> None:  # noqa: ANN001
        self.moments.append(moment)


class FakeTap:
    def __init__(self, *, will_sign_in: bool = True) -> None:
        self.egress = FakeEgress()
        self.will_sign_in = will_sign_in
        self.reasons: list[str] = []

    def sign_in_again(self, reason: str) -> bool:
        self.reasons.append(reason)
        return self.will_sign_in


class TappedStream(FakeStream):
    def __init__(self, tap: FakeTap) -> None:
        super().__init__()
        self._tap = tap


def test_a_dead_session_signs_in_again_once_then_resends_the_batch(waits, monkeypatch) -> None:
    opened: list[object] = []
    monkeypatch.setattr(streams, "open_str", lambda page, stream=None: opened.append(page))
    tap = FakeTap()
    page, stream = FakePage([[EXPIRED], [EXPIRED], [EXPIRED], [OK]]), TappedStream(tap)
    assert streams.graphql(page, BODIES, stream=stream) == [{"x": 1}, {"x": 1}]
    assert tap.reasons == ["SessionExpired on SegDayOfWeekTable, DailyTable"]
    assert opened == [page] and page.calls == 4
    assert stream.notices[-1][1] == "signed_in_again_ok"
    assert tap.egress.moments == ["check", "at SessionExpired"]


def test_without_signing_in_again_a_dead_session_still_fails(waits) -> None:
    tap = FakeTap(will_sign_in=False)
    with pytest.raises(streams.GraphQLError, match="SessionExpired"):
        streams.graphql(FakePage([[EXPIRED]]), BODIES, stream=TappedStream(tap))
    assert len(tap.reasons) == 1


def test_query_batches_pause_a_random_human_interval_between_them(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(streams.time, "sleep", slept.append)
    tap = FakeTap()
    tap.config = {"query_pause_seconds": 4}
    bodies = [{"operationName": f"Op{i}"} for i in range(10)]  # 3 batches of up to 4
    streams.graphql(FakePage([[OK]]), bodies, stream=TappedStream(tap))
    assert len(slept) == 2 and all(2.0 <= s <= 6.0 for s in slept)
    tap.config = {"query_pause_seconds": 0}
    slept.clear()
    streams.graphql(FakePage([[OK]]), bodies, stream=TappedStream(tap))
    assert slept == []
