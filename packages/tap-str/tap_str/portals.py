"""Brand portal entry: sign in through the brand's SSO and land on CoStar STR.

Each portal returns the browser tab that is showing product.costar.com/app/str.
Codes for the identity provider come from the TOTP secret, never from a push.

CoStar's hand-off page (www.costar.com/str-user-registration/auth/validate) occasionally
stalls instead of forwarding to STR. By then the SAML session is set, so the portal opens
STR directly in that tab; if that stalls too it starts the hand-off again once. Any stall
writes each open tab's URL, title, text, visible buttons and a small JPEG screenshot to
the log, so a failed run shows what the page displayed.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from singer_playwright.login import LoginRejected
from singer_playwright.sso_login import SsoCredentials, complete_sso

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

log = logging.getLogger(__name__)

STR_PREFIX = "https://product.costar.com/app/str"
STR_HOME = STR_PREFIX
# Lobby's "My Apps" link: IdP-initiated SSO straight into OnQ Insider's My Applications.
HILTON_MY_APPS = (
    "https://fd.hilton.com/idp/startSSO.ping?PartnerSpId=onqinsider"
    "&TargetResource=%2fInsider%2fMyApp%2fMyApplication.aspx%3f"
)
HILTON_MY_APPS_PREFIX = "https://onqinsider.hilton.com/Insider/MyApp/MyApplication.aspx"
HILTON_COSTAR_LINK = "CoStar Benchmark"

HANDOFF_WAIT_S = 90
DIRECT_WAIT_S = 60
HANDOFF_ATTEMPTS = 2
SCREENSHOT_MARKER = "STR_PORTAL_SCREENSHOT_JPEG_BASE64"
MAX_SCREENSHOT_BYTES = 150_000  # keeps one log line under Cloud Logging's entry limit

VISIBLE_CONTROLS_JS = """() => [...document.querySelectorAll('button,a,input[type=submit],[role=button]')]
  .filter((e) => e.offsetWidth || e.offsetHeight)
  .map((e) => (e.innerText || e.value || '').trim())
  .filter(Boolean)
  .slice(0, 25)"""


def _on_str(page: Page) -> bool:
    return page.url.startswith(STR_PREFIX)


def log_stall(context: BrowserContext, reason: str) -> None:
    """Write what every open tab shows. Never raises: diagnostics must not hide the failure."""
    log.warning("STR portal stalled: %s (%s tabs open)", reason, len(context.pages))
    for index, tab in enumerate(context.pages):
        try:
            url = tab.url.split("?", 1)[0]
            title = tab.title()
            text = tab.evaluate("() => document.body ? document.body.innerText.slice(0, 2000) : ''")
            controls = tab.evaluate(VISIBLE_CONTROLS_JS)
            log.warning(
                "STR portal tab %s: url=%s title=%r controls=%r text=%r", index, url, title, controls, text
            )
            shot = tab.screenshot(type="jpeg", quality=40, full_page=False)
            if len(shot) <= MAX_SCREENSHOT_BYTES:
                log.warning("%s tab=%s %s", SCREENSHOT_MARKER, index, base64.b64encode(shot).decode())
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("STR portal tab %s: could not capture (%s)", index, exc)


def _open_costar(page: Page) -> Page:
    link = page.get_by_text(HILTON_COSTAR_LINK, exact=True).first
    link.wait_for(state="visible", timeout=60_000)
    with page.context.expect_page(timeout=60_000) as opened:
        link.click()
    tab = opened.value
    tab.bring_to_front()
    return tab


def _reach_str(tab: Page, creds: SsoCredentials) -> bool:
    """Let the hand-off finish; if it stalls, open STR directly (the SAML session is set)."""
    try:
        # CoStar asks Hilton's IdP again (SAML); with the session above it usually passes straight through.
        complete_sso(tab, creds, done=_on_str, timeout_s=HANDOFF_WAIT_S)
        return True
    except LoginRejected as exc:
        log.warning("CoStar hand-off stalled at %s (%s); opening STR directly", tab.url.split("?", 1)[0], exc)
    try:
        tab.goto(STR_HOME, wait_until="domcontentloaded", timeout=60_000)
        complete_sso(tab, creds, done=_on_str, timeout_s=DIRECT_WAIT_S)
        return True
    except Exception as exc:  # noqa: BLE001 - a stalled or errored page is a failed attempt
        log.warning("Opening STR directly did not reach it (%s)", exc)
        return False


def hilton_lobby(page: Page, creds: SsoCredentials, *, timeout_s: float = 240) -> Page:
    """Hilton SSO -> OnQ My Applications -> CoStar Benchmark (opens a new tab) -> STR."""
    try:
        page.goto(HILTON_MY_APPS, wait_until="domcontentloaded", timeout=90_000)
        complete_sso(page, creds, done=lambda p: p.url.startswith(HILTON_MY_APPS_PREFIX), timeout_s=timeout_s)
    except Exception as exc:
        log_stall(page.context, f"Hilton sign-in: {exc}")
        raise
    log.info("Hilton: on My Applications")

    for attempt in range(1, HANDOFF_ATTEMPTS + 1):
        try:
            tab = _open_costar(page)
        except Exception as exc:
            log_stall(page.context, f"opening CoStar Benchmark (attempt {attempt}): {exc}")
            raise
        if _reach_str(tab, creds):
            tab.wait_for_load_state("domcontentloaded")
            log.info("Hilton: on CoStar STR%s", "" if attempt == 1 else f" (attempt {attempt})")
            return tab
        log_stall(page.context, f"CoStar did not open STR (attempt {attempt} of {HANDOFF_ATTEMPTS})")
        tab.close()
        page.bring_to_front()
    msg = f"CoStar did not open STR after {HANDOFF_ATTEMPTS} attempts; page captures are in the log"
    raise LoginRejected(msg)


PORTALS = {"hilton_lobby": hilton_lobby}
