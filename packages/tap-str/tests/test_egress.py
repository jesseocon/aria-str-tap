"""The outbound IP is logged at sign-in, on change, and never breaks a run."""

from __future__ import annotations

import logging

from tap_str.egress import EgressWatch


class Response:
    def __init__(self, ip: str) -> None:
        self.ip = ip

    def json(self) -> dict:
        return {"ip": self.ip}


class FakePage:
    def __init__(self, ips: list[str | Exception]) -> None:
        self.ips = ips
        self.context = self
        self.request = self

    def get(self, _url: str, timeout: int) -> Response:  # noqa: ARG002
        ip = self.ips.pop(0)
        if isinstance(ip, Exception):
            raise ip
        return Response(ip)


def test_addresses_are_logged_and_a_change_is_a_warning(caplog) -> None:
    watch = EgressWatch(logging.getLogger("t"), every_seconds=0)
    page = FakePage(["34.1.1.1", "34.1.1.1", "35.2.2.2"])
    with caplog.at_level(logging.INFO):
        watch.record(page, "at sign-in")
        watch.maybe_record(page)
        watch.record(page, "at SessionExpired")
    assert "Outbound IP at sign-in: 34.1.1.1" in caplog.text
    assert "(unchanged)" in caplog.text
    changed = [r for r in caplog.records if r.getMessage().startswith("Outbound IP changed")]
    assert changed and changed[0].levelno == logging.WARNING and "34.1.1.1 -> 35.2.2.2" in changed[0].getMessage()


def test_a_failed_lookup_is_logged_and_never_raises(caplog) -> None:
    watch = EgressWatch(logging.getLogger("t"))
    with caplog.at_level(logging.INFO):
        assert watch.record(FakePage([TimeoutError("no network")]), "at sign-in") is None
    assert "Outbound IP lookup failed" in caplog.text


def test_checks_are_throttled() -> None:
    watch = EgressWatch(logging.getLogger("t"), every_seconds=3600)
    page = FakePage(["34.1.1.1"])
    watch.record(page, "at sign-in")
    watch.maybe_record(page)  # within the hour: no second lookup (the fake would run out of answers)
