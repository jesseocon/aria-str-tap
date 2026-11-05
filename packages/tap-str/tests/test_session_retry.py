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

    def wait_for_function(self, *_a, **_kw) -> None:
        self.drawn_waits = getattr(self, "drawn_waits", 0) + 1

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
    assert page.drawn_waits == 1  # still waits for the app to draw


def test_reload_opens_str_again_even_when_on_it(waits) -> None:
    page = NavPage(aborts=0)
    page.url = streams.STR_HOME + "/dashboard"
    streams.open_str(page, FakeStream(), reload=True)
    assert page.gotos == 1 and page.drawn_waits == 1


class BlankPage(NavPage):
    """STR loaded but never draws (2026-11-04, Daytona)."""

    def wait_for_function(self, *_a, **_kw) -> None:
        raise TimeoutError("Timeout 30000ms exceeded.")

    def title(self) -> str:
        return ""

    def evaluate(self, _js: str, *_a) -> int:
        return 0

    @property
    def context(self):  # noqa: ANN201
        class Context:
            def cookies(self) -> list[dict]:
                return [{"name": "costar_sid", "domain": ".costar.com", "value": "secret"}, {"name": "x", "domain": "okta.com"}]

        return Context()


def test_a_blank_str_page_is_logged_not_fatal(waits, caplog) -> None:
    page = BlankPage()
    page.url = streams.STR_HOME
    with caplog.at_level(logging.WARNING):
        streams.open_str(page, FakeStream())
    assert "STR had drawn nothing after 30s" in caplog.text
    assert "text_chars=0 costar_cookies=['costar_sid']" in caplog.text
    assert "secret" not in caplog.text  # cookie names only, never values


NOT_READY = {
    "status": 401,
    "json": {
        "error": "Authentication required",
        "message": "Non-interactive route demanded authentication which could not be verified",
        "reason": "MissingCredentials",
    },
}


def test_missing_credentials_reopens_str_then_retries(waits, monkeypatch, caplog) -> None:
    reopened: list[bool] = []
    monkeypatch.setattr(streams, "open_str", lambda page, stream=None, *, reload=False: reopened.append(reload))
    page, stream = FakePage([[NOT_READY], [OK]]), FakeStream()
    with caplog.at_level(logging.WARNING):
        data = streams.graphql(page, BODIES[:1], stream=stream)
    assert data == [{"x": 1}]
    assert reopened == [True] and waits == [5]
    assert stream.notices[-1] == ("CoStar said MissingCredentials on SegDayOfWeekTable; retry 1 worked", "session_retry_ok")
    assert "CoStar refused SegDayOfWeekTable: " in caplog.text


def test_missing_credentials_that_never_clears_fails_after_the_retries(waits, monkeypatch) -> None:
    monkeypatch.setattr(streams, "open_str", lambda page, stream=None, **_kw: None)
    page = FakePage([[NOT_READY]])
    with pytest.raises(streams.GraphQLError, match="401.*MissingCredentials"):
        streams.graphql(page, BODIES[:1], stream=FakeStream())
    assert page.calls == 1 + len(streams.RETRY_WAITS_SECONDS)


def test_other_401s_are_not_retried(waits) -> None:
    page = FakePage([[{"status": 401, "json": {"reason": "Forbidden"}}]])
    with pytest.raises(streams.GraphQLError, match="401"):
        streams.graphql(page, BODIES[:1], stream=FakeStream())
    assert page.calls == 1


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
    monkeypatch.setattr(streams, "open_str", lambda page, stream=None, **_kw: opened.append(page))
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
    pauses = [slept[i] + slept[i + 1] for i in range(0, len(slept), 2)]  # each pause is split by a mouse move
    assert len(pauses) == 2 and all(2.0 <= s <= 6.0 for s in pauses)
    tap.config = {"query_pause_seconds": 0}
    slept.clear()
    streams.graphql(FakePage([[OK]]), bodies, stream=TappedStream(tap))
    assert slept == []


SIGNED_OUT_URL = "https://product.costar.com/app/signout?r=InactivityTimeout&return_path=%2Fapp%2Fstr%2Fdashboard"


class LeavingPage(FakePage):
    """The first batch dies when CoStar's inactivity sign-out navigates the page away."""

    def __init__(self) -> None:
        super().__init__([[OK]])
        self.url = SIGNED_OUT_URL
        self.left = False

    def evaluate(self, js: str, args: dict) -> list[dict]:
        if not self.left:
            self.left = True
            msg = "Page.evaluate: Execution context was destroyed, most likely because of a navigation."
            raise RuntimeError(msg)
        return super().evaluate(js, args)


def test_an_inactivity_sign_out_reopens_str_and_resends_the_batch(waits, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(streams, "open_str", lambda page, stream=None, **_kw: opened.append(page.url))
    page, stream = LeavingPage(), FakeStream()
    assert streams.graphql(page, BODIES, stream=stream) == [{"x": 1}, {"x": 1}]
    assert opened == [SIGNED_OUT_URL]
    assert stream.screens == ["CoStar signed the page out for inactivity"]
    assert stream.notices[0][1] == "reopened_str"


def test_a_page_that_reopens_on_the_login_page_signs_in_again(waits, monkeypatch) -> None:
    calls: list[str] = []

    def open_str(page, stream=None, **_kw) -> None:
        calls.append("open")
        if len(calls) == 1:
            raise LoginRejected("CoStar returned to its login page after sign-in")

    class Tap:
        def sign_in_again(self, reason: str) -> bool:
            calls.append(f"sign in: {reason}")
            return True

    monkeypatch.setattr(streams, "open_str", open_str)
    page, stream = LeavingPage(), FakeStream()
    stream._tap = Tap()
    streams.graphql(page, BODIES, stream=stream)
    assert calls == ["open", "sign in: CoStar signed the page out for inactivity", "open"]


def test_pauses_move_the_mouse(waits) -> None:
    class Mouse:
        moves: list[tuple[float, float]] = []

        def move(self, x: float, y: float, *, steps: int) -> None:
            assert steps >= 5
            self.moves.append((x, y))

    class Page:
        viewport_size = {"width": 1000, "height": 600}
        mouse = Mouse()

    streams.pause(None, Page())
    assert len(Mouse.moves) == 1 and len(waits) == 2
    x, y = Mouse.moves[0]
    assert 200 <= x <= 800 and 180 <= y <= 480


# What CoStar sent on 2026-11-04 (AC Phoenix Downtown, Saint Rose): a 200 whose error says the
# service behind the GraphQL gateway failed.
def _outage(status: str = "500: Internal Server Error", path: str = "benchmarking/compset/segmentation/monthlydayofweek") -> dict:
    return {
        "status": 200,
        "json": {
            "errors": [
                {
                    "message": f"Unexpected error while fetching data from {path}: {status}",
                    "locations": [{"line": 4, "column": 5}],
                    "path": ["SegDayOfWeekTableData", "compSet"],
                    "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                }
            ]
        },
    }


OUTAGE = _outage()


def test_a_costar_service_error_waits_then_resends_only_the_failed_query(waits, caplog) -> None:
    page, stream = FakePage([[OUTAGE, OK], [{"status": 200, "json": {"data": {"y": 2}}}]]), FakeStream()
    with caplog.at_level(logging.WARNING):
        data = streams.graphql(page, BODIES, stream=stream)
    assert data == [{"y": 2}, {"x": 1}]  # the retried answer goes back in its own place
    assert page.calls == 2
    assert sum(waits) == streams.UPSTREAM_RETRY_WAITS_SECONDS[0]
    assert stream.screens == ["CoStar service error on SegDayOfWeekTable"]
    assert stream.notices[-1][1] == "costar_retry_ok"
    assert "monthlydayofweek: 500: Internal Server Error); waiting 20s, then retrying (1 of 3)" in caplog.text


def test_a_503_that_clears_on_the_last_retry_carries_on(waits) -> None:
    flaky = _outage("503: Service Unavailable", "benchmarking/industry/segmentation/monthlydayofweek")
    page = FakePage([[flaky, OK], [flaky], [flaky], [OK]])
    assert streams.graphql(page, BODIES, stream=FakeStream()) == [{"x": 1}, {"x": 1}]
    assert page.calls == 4 and sum(waits) == sum(streams.UPSTREAM_RETRY_WAITS_SECONDS)


def test_a_costar_outage_that_lasts_fails_as_retryable_with_a_plain_message(waits) -> None:
    page = FakePage([[OUTAGE]])
    with pytest.raises(streams.CoStarUnavailable) as raised:
        streams.graphql(page, BODIES[:1], stream=FakeStream())
    assert raised.value.retryable is True
    assert isinstance(raised.value, streams.GraphQLError)
    message = str(raised.value)
    assert "SegDayOfWeekTable 4 times over 3 min" in message
    assert "monthlydayofweek: 500: Internal Server Error" in message
    assert "outage on CoStar's side" in message
    assert page.calls == 1 + len(streams.UPSTREAM_RETRY_WAITS_SECONDS)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_gateway_overload_statuses_are_retried(waits, status) -> None:
    page = FakePage([[{"status": status, "json": None}], [OK]])
    assert streams.graphql(page, BODIES[:1], stream=FakeStream()) == [{"x": 1}]
    assert page.calls == 2


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 200, "json": {"errors": [{"message": "Cannot query field 'x' on type 'Query'."}]}},
        {"status": 200, "json": {"errors": [OUTAGE["json"]["errors"][0], {"message": "Variable $id is invalid"}]}},
        {"status": 500, "json": {"errors": ["boom"]}},
        {"status": 400, "json": None},
    ],
)
def test_query_errors_are_not_mistaken_for_an_outage(waits, answer) -> None:
    page = FakePage([[answer]])
    with pytest.raises(streams.GraphQLError) as raised:
        streams.graphql(page, BODIES[:1], stream=FakeStream())
    assert not isinstance(raised.value, streams.CoStarUnavailable)
    assert waits == [] and page.calls == 1


def test_a_session_expired_retry_inside_an_outage_retry_still_signs_nothing_in(waits) -> None:
    # The resent query meets a 402 first; the SessionExpired waits handle it on the same session.
    page = FakePage([[OUTAGE], [EXPIRED], [OK]])
    assert streams.graphql(page, BODIES[:1], stream=FakeStream()) == [{"x": 1}]
    assert page.calls == 3
