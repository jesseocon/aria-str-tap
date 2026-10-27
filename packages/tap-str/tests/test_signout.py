"""The tap ends its CoStar session whether the sync finishes or fails, and proves it in the log."""

from __future__ import annotations

import logging

import pytest

from tap_str import signout
from tap_str.tap import TapStr

SIGN_OUT_HREF = "https://product.costar.com/home/signout"
LOGIN = "https://secure.costargroup.com/login?signin=x"


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    def _missing(self) -> bool:
        if not self.page.live:  # a closed session's page has no menu or Sign Out link
            return True
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
        self.page.end_session()

    def get_attribute(self, _name: str) -> str | None:
        return self.page.href


class FakePage:
    """A CoStar tab. `live` is the server-side session; sign-out ends it unless `sticky`."""

    def __init__(
        self,
        url: str,
        href: str | None = SIGN_OUT_HREF,
        *,
        menu: bool = True,
        live: bool = True,
        sticky: bool = False,
    ) -> None:
        self.url = url
        self.href = href
        self.menu = menu
        self.live = live
        self.sticky = sticky
        self.menu_open = False
        self.clicks: list[str] = []
        self.visited: list[str] = []

    def end_session(self) -> None:
        self.url = "https://www.costar.com/"
        if not self.sticky:
            self.live = False

    def locator(self, selector: str) -> FakeLocator:
        assert selector in (signout.MENU_BUTTON, signout.SIGN_OUT_LINK)
        return FakeLocator(self, selector)

    def goto(self, url: str, **_kw: object) -> None:
        self.visited.append(url)
        if url == SIGN_OUT_HREF:
            self.end_session()
        elif url == signout.DASHBOARD:
            self.url = url if self.live else LOGIN
        else:
            self.url = url

    def wait_for_url(self, predicate, **_kw: object) -> None:  # noqa: ANN001
        if not predicate(self.url):
            raise TimeoutError(self.url)

    def is_closed(self) -> bool:
        return False


DASH = signout.DASHBOARD


def test_sign_out_opens_the_menu_clicks_sign_out_and_confirms() -> None:
    page = FakePage(DASH)
    assert signout.sign_out(page) == "menu; confirmed: dashboard asks for login"
    assert page.clicks == ["Sign Out"] and page.visited == [DASH]
    assert not page.live


def test_without_the_menu_it_follows_the_sign_out_link_and_confirms() -> None:
    page = FakePage(DASH, menu=False)
    assert signout.sign_out(page) == "Sign Out link; confirmed: dashboard asks for login"
    assert page.visited == [SIGN_OUT_HREF, DASH]


def test_a_session_costar_already_ended_is_reported_not_failed() -> None:
    # SessionExpired mid-run: the page has no menu or link, and the dashboard asks for login.
    page = FakePage(DASH, live=False)
    assert signout.sign_out(page).startswith("CoStar session had already ended")


def test_still_signed_in_after_sign_out_fails_loudly() -> None:
    page = FakePage(DASH, sticky=True)
    with pytest.raises(signout.SignOutFailed, match="dashboard still opened"):
        signout.sign_out(page)


def test_no_way_out_while_signed_in_says_why() -> None:
    page = FakePage("https://product.costar.com/app/str", href=None, menu=False)
    with pytest.raises(signout.SignOutFailed, match="menu: .*link: "):
        signout.sign_out(page)


def test_already_on_the_login_page_is_signed_out() -> None:
    page = FakePage(LOGIN, live=False)
    assert signout.sign_out(page).startswith("already on the login page")
    assert page.visited == [] and page.clicks == []


class FakeRuntime:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _tap(page: FakePage, *, verified: bool = True) -> tuple[TapStr, FakeRuntime]:
    tap = TapStr(config={"property_id": "5675029"}, validate_config=False)
    runtime = FakeRuntime(page)
    tap._browser_runtime = runtime  # noqa: SLF001
    tap._session_verified = verified  # noqa: SLF001
    return tap, runtime


def test_a_failed_sync_still_signs_out_then_closes_the_browser(monkeypatch, caplog) -> None:
    page = FakePage(DASH)
    tap, runtime = _tap(page)

    def boom(_self: object) -> None:
        raise RuntimeError("SessionExpired")

    monkeypatch.setattr("singer_playwright.tap.PlaywrightTap.sync_all", boom)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="SessionExpired"):
        tap.sync_all()
    assert not page.live
    assert "Signed out of CoStar (menu; confirmed: dashboard asks for login)" in caplog.text
    assert runtime.stopped and tap._browser_runtime is None  # noqa: SLF001


def test_a_sign_out_that_fails_raises_a_warning_event(caplog) -> None:
    tap, runtime = _tap(FakePage(DASH, sticky=True))
    with caplog.at_level(logging.INFO):
        tap.teardown()
    assert "Signing out of CoStar failed" in caplog.text
    event = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("ARIA "))
    assert '"kind":"warning"' in event.replace(" ", "") and "sign_out_failed" in event
    assert runtime.stopped


def test_a_run_that_never_signed_in_says_so(caplog) -> None:
    tap, runtime = _tap(FakePage("about:blank"), verified=False)
    with caplog.at_level(logging.INFO):
        tap.teardown()
    assert "never signed in; nothing to sign out" in caplog.text
    assert runtime.stopped


def test_the_portal_tab_is_the_one_signed_out() -> None:
    first = FakePage("https://lobbylite.hilton.com/")
    tab = FakePage(DASH)
    tap, runtime = _tap(first)
    tap._portal_page = tab  # noqa: SLF001
    tap.teardown()
    assert not tab.live and first.live
    assert runtime.stopped


def test_signing_in_again_happens_once_and_only_for_direct_logins(monkeypatch) -> None:
    signed_in: list[str] = []
    monkeypatch.setattr("tap_str.tap.apply_credentials", lambda page, **kw: signed_in.append(kw["username"]))
    tap, _runtime = _tap(FakePage(DASH, live=False))
    tap._config = {**tap.config, "username": "u", "password": "p", "access_path": "direct"}  # noqa: SLF001
    monkeypatch.setattr(type(tap), "egress", property(lambda _self: type("E", (), {"record": lambda *a: None})()))
    assert tap.sign_in_again("SessionExpired on X") is True
    assert tap.sign_in_again("SessionExpired on Y") is False
    assert signed_in == ["u"]

    lobby, _ = _tap(FakePage(DASH))
    lobby._config = {**lobby.config, "username": "u", "password": "p", "access_path": "hilton_lobby"}  # noqa: SLF001
    assert lobby.sign_in_again("SessionExpired on X") is False


class ChainContext:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def storage_state(self, *, path: str) -> None:
        self.saved.append(path)
        open(path, "w").write('{"cookies": [{"name": "costar"}], "origins": []}')


def test_in_a_login_chain_the_session_is_handed_on_not_signed_out(tmp_path, caplog) -> None:
    page = FakePage(DASH)
    tap, runtime = _tap(page)
    runtime.context = ChainContext()
    handoff = tmp_path / "storage_state.json"
    tap._config = {**tap.config, "handoff_path": str(handoff), "access_path": "direct"}  # noqa: SLF001
    with caplog.at_level(logging.INFO):
        tap.teardown()
    assert page.live and page.clicks == []  # not signed out
    assert handoff.read_text().startswith('{"cookies"') and not (tmp_path / "storage_state.json.part").exists()
    assert "Handed the CoStar session to the next run" in caplog.text
    assert runtime.stopped


def test_a_run_handed_a_live_session_starts_signed_in_without_a_password(tmp_path, monkeypatch, caplog) -> None:
    import tap_str.tap as tap_module

    started: list[str | None] = []

    class Runtime:
        def __init__(self, config) -> None:  # noqa: ANN001
            started.append(config.storage_state_path)
            self.page = FakePage(DASH)

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    handoff = tmp_path / "storage_state.json"
    handoff.write_text('{"cookies": [], "origins": []}')
    monkeypatch.setattr(tap_module, "BrowserRuntime", Runtime)
    tap = TapStr(config={"property_id": "1", "handoff_path": str(handoff), "access_path": "direct"}, validate_config=False)
    with caplog.at_level(logging.INFO):
        tap._ensure_browser_started()  # noqa: SLF001
    assert started == [str(handoff)] and tap._session_verified  # noqa: SLF001
    assert "no new sign-in" in caplog.text


def test_a_dead_handed_over_session_falls_back_to_the_password(tmp_path, monkeypatch) -> None:
    import tap_str.tap as tap_module
    from singer_playwright.tap import PlaywrightTap

    class Runtime:
        def __init__(self, config) -> None:  # noqa: ANN001
            self.page = FakePage(DASH, live=False)

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    signed_in: list[bool] = []

    def password_start(self) -> None:  # noqa: ANN001
        signed_in.append(True)
        self._session_verified = True

    handoff = tmp_path / "storage_state.json"
    handoff.write_text('{"cookies": [], "origins": []}')
    monkeypatch.setattr(tap_module, "BrowserRuntime", Runtime)
    monkeypatch.setattr(PlaywrightTap, "_ensure_browser_started", password_start)
    tap = TapStr(config={"property_id": "1", "handoff_path": str(handoff), "access_path": "direct"}, validate_config=False)
    tap._ensure_browser_started()  # noqa: SLF001
    assert signed_in == [True]
