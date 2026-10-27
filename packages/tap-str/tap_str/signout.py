"""End the CoStar session when a run finishes.

CoStar allows one session per account: a session left open blocks nothing on its own,
but the next sign-in anywhere (a person, or another run) ends it, and a person signing
in mid-run ends the run's. Signing out keeps the account's one session for whoever
needs it next.

Sign Out sits in the hamburger menu as a plain link (observed 2026-10-27). The tap opens
the menu and clicks it, like a person; if the menu won't open it loads the address the
link carries, which is what the click does. Either way it then reloads the STR dashboard
and requires CoStar's login page, so the run log proves the session is closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

MENU_BUTTON = "#csgp-menu-button"
SIGN_OUT_LINK = 'a[href*="/home/signout"]'
SIGNED_IN_HOST = "product.costar.com"
LOGIN_HOST = "secure.costargroup.com"
DASHBOARD = "https://product.costar.com/app/str/dashboard"


class SignOutFailed(RuntimeError):
    pass


def _signed_out(page: Page, timeout_ms: int) -> None:
    page.wait_for_url(lambda url: SIGNED_IN_HOST not in url, timeout=timeout_ms)


def _click_through_menu(page: Page, timeout_ms: int) -> None:
    """What a person does: open the hamburger menu, then click Sign Out."""
    page.locator(MENU_BUTTON).first.click(timeout=timeout_ms)
    link = page.locator(SIGN_OUT_LINK).first
    link.wait_for(state="visible", timeout=timeout_ms)
    link.click(timeout=timeout_ms)
    _signed_out(page, timeout_ms)


def _follow_link(page: Page, timeout_ms: int) -> None:
    """Load the address the Sign Out link carries, for when the menu won't open."""
    link = page.locator(SIGN_OUT_LINK).first
    link.wait_for(state="attached", timeout=timeout_ms)
    href = link.get_attribute("href")
    if not href:
        msg = "the Sign Out link has no address"
        raise SignOutFailed(msg)
    page.goto(href, wait_until="load", timeout=timeout_ms)
    _signed_out(page, timeout_ms)


def session_closed(page: Page, *, timeout_ms: int = 30_000) -> bool:
    """Load the STR dashboard: a closed session is sent to CoStar's login page."""
    try:
        page.goto(DASHBOARD, wait_until="load", timeout=timeout_ms)
        page.wait_for_url(lambda url: LOGIN_HOST in url, timeout=timeout_ms)
    except Exception:  # noqa: BLE001 - still on the dashboard means still signed in
        return LOGIN_HOST in page.url
    return True


def sign_out(page: Page, *, timeout_ms: int = 30_000) -> str:
    """Sign out through the menu, else by following the link, then prove the session is closed.

    Returns what happened, for the run log. Raises SignOutFailed while still signed in.
    """
    if LOGIN_HOST in page.url:
        return "already on the login page; nothing to sign out"
    at = page.url
    try:
        _click_through_menu(page, timeout_ms)
        how = "menu"
    except Exception as menu_exc:  # noqa: BLE001 - fall back to the link
        try:
            _follow_link(page, timeout_ms)
            how = "Sign Out link"
        except Exception as exc:
            # No menu and no link: CoStar may have ended the session already (SessionExpired).
            if session_closed(page, timeout_ms=timeout_ms):
                return "CoStar session had already ended; nothing to sign out (confirmed: dashboard asks for login)"
            msg = (
                f"from {at}: menu: {str(menu_exc).splitlines()[0]}; "
                f"link: {str(exc).splitlines()[0]}"
            )
            raise SignOutFailed(msg) from exc
    if not session_closed(page, timeout_ms=timeout_ms):
        msg = f"signed out via {how}, but the STR dashboard still opened ({page.url})"
        raise SignOutFailed(msg)
    return f"{how}; confirmed: dashboard asks for login"
