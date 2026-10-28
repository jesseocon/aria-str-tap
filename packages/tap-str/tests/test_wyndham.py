"""Wyndham Community: Okta username, emailed code from the code inbox, then CoStar's hand-off."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tap_str import portals

CREDS = portals.OktaEmailCodeCredentials(username="jesse.ocon@wyndham.com", code_address="wyndham-okta@aria-ingest.com")


class Element:
    def __init__(self, page: OktaPage, selector: str) -> None:
        self.page = page
        self.selector = selector

    def wait_for(self, *, state: str, timeout: int) -> None:  # noqa: ARG002
        self.page.steps.append(f"wait {self.selector}")

    def fill(self, value: str) -> None:
        self.page.steps.append(f"fill {self.selector}={value}")

    def click(self, **_kw) -> None:
        self.page.steps.append(f"click {self.selector}")
        if self.selector == portals.OKTA_SEND_EMAIL:
            self.page.clicked_send_at = datetime.now(UTC)
        if self.selector == portals.OKTA_VERIFY:
            self.page.url = "https://community.wyndham.com/"


class OktaPage:
    def __init__(self) -> None:
        self.url = ""
        self.steps: list[str] = []
        self.clicked_send_at: datetime | None = None
        self.context = self
        self.pages: list = []

    def goto(self, url: str, **_kw) -> None:
        self.steps.append(f"goto {url}")
        self.url = "https://sign-in.wyndham.com/oauth2/v1/authorize" if "community" in url else url
        if url == portals.WYNDHAM_STR_HANDOFF:
            self.url = "https://product.costar.com/app/str/detail/5872229/star/month/glance"

    def locator(self, selector: str) -> Element:
        return Element(self, selector)

    def wait_for_url(self, predicate, **_kw) -> None:  # noqa: ANN001
        if not predicate(self.url):
            raise TimeoutError(self.url)

    def wait_for_load_state(self, *_a, **_kw) -> None:
        return None


def test_signs_in_with_the_emailed_code_then_opens_str(monkeypatch) -> None:
    asked: list[tuple[str, datetime]] = []

    def fake_wait_for_code(address: str, *, after: datetime, timeout_s: float) -> str:
        asked.append((address, after))
        return "482915"

    monkeypatch.setattr(portals, "wait_for_code", fake_wait_for_code)
    page = OktaPage()
    tab = portals.wyndham_community(page, CREDS)

    assert tab is page and page.url.startswith(portals.STR_PREFIX)
    clicks = [s for s in page.steps if s.startswith(("fill", "click", "goto"))]
    assert clicks == [
        f"goto {portals.WYNDHAM_COMMUNITY}",
        f"fill {portals.OKTA_USERNAME}=jesse.ocon@wyndham.com",
        f"click {portals.OKTA_NEXT}",
        f"click {portals.OKTA_SEND_EMAIL}",
        f"click {portals.OKTA_CODE_INSTEAD}",
        f"fill {portals.OKTA_CODE}=482915",
        f"click {portals.OKTA_VERIFY}",
        f"goto {portals.WYNDHAM_STR_HANDOFF}",
    ]
    address, after = asked[0]
    assert address == "wyndham-okta@aria-ingest.com"
    assert after <= page.clicked_send_at  # only codes sent after "Send me an email" count


class SessionPage:
    def __init__(self, *, live: bool, ends: bool = True) -> None:
        self.live = live
        self.ends = ends
        self.url = ""
        self.deleted = False

    def goto(self, url: str, **_kw) -> None:
        self.url = url if self.live else "https://sign-in.wyndham.com/oauth2/v1/authorize?client_id=okta"

    def evaluate(self, script: str) -> int:
        assert "DELETE" in script and "/api/v1/sessions/me" in script
        self.deleted = True
        self.live = not self.ends
        return 204


def test_okta_sign_out_ends_the_session_and_confirms() -> None:
    page = SessionPage(live=True)
    assert portals.okta_sign_out(page).startswith("Okta session ended; confirmed")
    assert page.deleted


def test_okta_sign_out_reports_an_ended_session_and_refuses_a_stuck_one() -> None:
    assert portals.okta_sign_out(SessionPage(live=False)) == "Okta session had already ended"
    with pytest.raises(portals.LoginRejected, match="still open"):
        portals.okta_sign_out(SessionPage(live=True, ends=False))


class RegisteringPage(OktaPage):
    """The hand-off shows CoStar's Benchmark registration page, then redirects to STR."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def goto(self, url: str, **kw) -> None:
        super().goto(url, **kw)
        if url == portals.WYNDHAM_STR_HANDOFF:
            self.url = "https://www.costar.com/str-user-registration/auth/validate?mode=wyndham"

    def wait_for_url(self, predicate, *, timeout: float = 0, **_kw) -> None:  # noqa: ANN001
        if "str-user-registration" in self.url:
            self.waits.append(timeout)
            if timeout < portals.REGISTRATION_WAIT_S * 1000:
                raise TimeoutError(self.url)  # the normal wait runs out
            self.url = "https://product.costar.com/app/str/dashboard"  # CoStar redirects by itself
        super().wait_for_url(predicate)

    def evaluate(self, _script: str) -> str:
        return "You are almost done! Please allow approximately 10 minutes for us to process your Benchmark registration."


def test_a_benchmark_registration_page_is_waited_out_not_failed(monkeypatch) -> None:
    monkeypatch.setattr(portals, "wait_for_code", lambda *_a, **_kw: "482915")
    page = RegisteringPage()
    assert portals.wyndham_community(page, CREDS) is page
    assert page.url.startswith(portals.STR_PREFIX)
    assert page.waits[-1] == portals.REGISTRATION_WAIT_S * 1000
