"""The tap ends its CoStar session whether the sync finishes or fails."""

from __future__ import annotations

import pytest

from tap_str import signout
from tap_str.tap import TapStr

SIGN_OUT_HREF = "https://product.costar.com/home/signout"


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    def _missing(self) -> bool:
        if self.selector == signout.MENU_BUTTON:
            return not self.page.menu
        return self.page.href is None

    def wait_for(self, *, state: str = "visible", **_kw: object) -> None:
        if self._missing() or (state == "visible" and not self.page.menu_open):
            raise TimeoutError(f"{self.selector} not {state}")

    def click(self, **_kw: object) -> None:
        self.wait_for(state="attached")
        if self.selector == signout.MENU_BUTTON:
            self.page.menu_open = True
            return
        self.page.clicks.append("Sign Out")
        self.page.url = "https://www.costar.com/"

    def get_attribute(self, _name: str) -> str | None:
        return self.page.href


class FakePage:
    def __init__(self, url: str, href: str | None = SIGN_OUT_HREF, *, menu: bool = True) -> None:
        self.url = url
        self.href = href
        self.menu = menu
        self.menu_open = False
        self.clicks: list[str] = []
        self.visited: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        assert selector in (signout.MENU_BUTTON, signout.SIGN_OUT_LINK)
        return FakeLocator(self, selector)

    def goto(self, url: str, **_kw: object) -> None:
        self.visited.append(url)
        self.url = "https://www.costar.com/" if url == SIGN_OUT_HREF else url

    def wait_for_url(self, predicate, **_kw: object) -> None:  # noqa: ANN001
        if not predicate(self.url):
            raise TimeoutError(self.url)

    def is_closed(self) -> bool:
        return False

    @property
    def signed_out(self) -> bool:
        return self.clicks == ["Sign Out"] or self.visited == [SIGN_OUT_HREF]


def test_sign_out_opens_the_menu_and_clicks_sign_out() -> None:
    page = FakePage("https://product.costar.com/app/str/dashboard")
    assert signout.sign_out(page) == "menu"
    assert page.clicks == ["Sign Out"] and page.visited == []


def test_without_the_menu_it_follows_the_sign_out_link() -> None:
    page = FakePage("https://product.costar.com/app/str/dashboard", menu=False)
    assert signout.sign_out(page) == "Sign Out link"
    assert page.visited == [SIGN_OUT_HREF]


def test_already_on_the_login_page_is_signed_out() -> None:
    page = FakePage("https://secure.costargroup.com/login?signin=x")
    assert signout.sign_out(page) == "already signed out"
    assert page.visited == [] and page.clicks == []


def test_no_way_out_says_why() -> None:
    page = FakePage("https://product.costar.com/app/str", href=None, menu=False)
    with pytest.raises(signout.SignOutFailed, match="menu: .*link: "):
        signout.sign_out(page)


class FakeRuntime:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _tap(page: FakePage) -> tuple[TapStr, FakeRuntime]:
    tap = TapStr(config={"property_id": "5675029"}, validate_config=False)
    runtime = FakeRuntime(page)
    tap._browser_runtime = runtime  # noqa: SLF001
    tap._session_verified = True  # noqa: SLF001
    return tap, runtime


def test_a_failed_sync_still_signs_out_then_closes_the_browser(monkeypatch) -> None:
    page = FakePage("https://product.costar.com/app/str/dashboard")
    tap, runtime = _tap(page)

    def boom(_self: object) -> None:
        raise RuntimeError("SessionExpired")

    monkeypatch.setattr("singer_playwright.tap.PlaywrightTap.sync_all", boom)
    with pytest.raises(RuntimeError, match="SessionExpired"):
        tap.sync_all()
    assert page.signed_out
    assert runtime.stopped and tap._browser_runtime is None  # noqa: SLF001


def test_the_portal_tab_is_the_one_signed_out() -> None:
    first = FakePage("https://lobbylite.hilton.com/")
    tab = FakePage("https://product.costar.com/app/str/dashboard")
    tap, runtime = _tap(first)
    tap._portal_page = tab  # noqa: SLF001
    tap.teardown()
    assert tab.signed_out and not first.signed_out
    assert runtime.stopped
