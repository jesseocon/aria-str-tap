"""End the CoStar session when a run finishes.

CoStar allows one session per account: a session left open blocks nothing on its own,
but the next sign-in anywhere (a person, or another run) ends it, and a person signing
in mid-run ends the run's. Signing out keeps the account's one session for whoever
needs it next.

Sign Out sits in the hamburger menu as a plain link (observed 2026-10-27). The tap opens
the menu and clicks it, like a person; if the menu won't open it loads the address the
link carries, which is what the click does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

MENU_BUTTON = "#csgp-menu-button"
SIGN_OUT_LINK = 'a[href*="/home/signout"]'
SIGNED_IN_HOST = "product.costar.com"
LOGIN_HOST = "secure.costargroup.com"


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


def sign_out(page: Page, *, timeout_ms: int = 30_000) -> str:
    """Sign out through the menu, else by following the link. Returns which way worked."""
    if LOGIN_HOST in page.url:
        return "already signed out"
    at = page.url
    try:
        _click_through_menu(page, timeout_ms)
    except Exception as menu_exc:  # noqa: BLE001 - fall back to the link
        try:
            _follow_link(page, timeout_ms)
        except Exception as exc:
            msg = (
                f"from {at}: menu: {str(menu_exc).splitlines()[0]}; "
                f"link: {str(exc).splitlines()[0]}"
            )
            raise SignOutFailed(msg) from exc
        return "Sign Out link"
    return "menu"
