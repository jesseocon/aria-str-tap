"""Brand portal entry: sign in through the brand's SSO and land on CoStar STR.

Each portal returns the browser tab that is showing product.costar.com/app/str.
Hilton's identity provider takes a code from the TOTP secret, never from a push.
Wyndham's Okta emails a code; it reaches the run through Aria's code inbox
(singer_playwright.codes.wait_for_code), never a person.

CoStar's hand-off page (www.costar.com/str-user-registration/auth/validate) occasionally
stalls instead of forwarding to STR. By then the SAML session is set, so the portal opens
STR directly in that tab; if that stalls too it starts the hand-off again once. Any stall
writes each open tab's URL, title, text, visible buttons and a small JPEG screenshot to
the log, so a failed run shows what the page displayed.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from singer_playwright.codes import wait_for_code
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


# Wyndham Community (observed 2026-10-27): Okta at sign-in.wyndham.com, username then an
# emailed code (no password), then the portal's "STR Benchmark" link, CoStar's hand-off page.
WYNDHAM_COMMUNITY = "https://community.wyndham.com/"
WYNDHAM_SIGN_IN = "https://sign-in.wyndham.com"
WYNDHAM_STR_HANDOFF = "https://www.costar.com/str-user-registration/auth/validate?mode=wyndham"
OKTA_USERNAME = 'input[name="identifier"]'
OKTA_NEXT = 'input[type="submit"][value="Next"]'
OKTA_SEND_EMAIL = 'input[type="submit"][value="Send me an email"]'
OKTA_CODE_INSTEAD = "button.enter-auth-code-instead-link"
OKTA_CODE = 'input[name="credentials.passcode"]'
OKTA_VERIFY = 'input[type="submit"][value="Verify"]'
CODE_WAIT_S = 240


@dataclass(frozen=True)
class OktaEmailCodeCredentials:
    """An Okta username whose second step is a code emailed to an inbox forwarded to Aria."""

    username: str
    code_address: str


def okta_email_code_sign_in(page: Page, creds: OktaEmailCodeCredentials, *, timeout_ms: int = 60_000) -> None:
    """Username, "Send me an email", "Enter a verification code instead", the code, Verify."""
    page.goto(WYNDHAM_COMMUNITY, wait_until="domcontentloaded", timeout=90_000)
    username = page.locator(OKTA_USERNAME)
    username.wait_for(state="visible", timeout=timeout_ms)
    username.fill(creds.username)
    page.locator(OKTA_NEXT).click(timeout=timeout_ms)
    send = page.locator(OKTA_SEND_EMAIL)
    send.wait_for(state="visible", timeout=timeout_ms)
    # A few seconds' grace: the inbox records arrival time, the click happens after this.
    asked = datetime.now(UTC) - timedelta(seconds=5)
    send.click(timeout=timeout_ms)
    instead = page.locator(OKTA_CODE_INSTEAD)
    instead.wait_for(state="visible", timeout=timeout_ms)
    instead.click(timeout=timeout_ms)
    box = page.locator(OKTA_CODE)
    box.wait_for(state="visible", timeout=timeout_ms)
    log.info("Wyndham: waiting for the emailed code at %s", creds.code_address)
    code = wait_for_code(creds.code_address, after=asked, timeout_s=CODE_WAIT_S)
    box.fill(code)
    page.locator(OKTA_VERIFY).click(timeout=timeout_ms)
    page.wait_for_url(lambda url: not url.startswith(WYNDHAM_SIGN_IN) or "/app/UserHome" in url, timeout=timeout_ms)
    log.info("Wyndham: signed in to Okta")


def okta_sign_out(page: Page, *, timeout_ms: int = 30_000) -> str:
    """End the Okta session the way the portal does (DELETE /api/v1/sessions/me), and confirm."""
    page.goto(f"{WYNDHAM_SIGN_IN}/app/UserHome", wait_until="domcontentloaded", timeout=timeout_ms)
    if "/app/UserHome" not in page.url:
        return "Okta session had already ended"
    status = page.evaluate(
        """async () => {
          const resp = await fetch('/api/v1/sessions/me', {method: 'DELETE', credentials: 'include',
            headers: {accept: 'application/json'}});
          return resp.status;
        }"""
    )
    page.goto(f"{WYNDHAM_SIGN_IN}/app/UserHome", wait_until="domcontentloaded", timeout=timeout_ms)
    if "/app/UserHome" in page.url:
        msg = f"Okta still open after sign-out (status {status})"
        raise LoginRejected(msg)
    return "Okta session ended; confirmed: the dashboard asks for sign-in"


def wyndham_community(page: Page, creds: OktaEmailCodeCredentials, *, timeout_s: float = 240) -> Page:
    """Okta (emailed code) -> CoStar's STR hand-off -> STR, all in this tab."""
    try:
        okta_email_code_sign_in(page, creds)
    except Exception as exc:
        log_stall(page.context, f"Wyndham sign-in: {exc}")
        raise
    for attempt in range(1, HANDOFF_ATTEMPTS + 1):
        try:
            page.goto(WYNDHAM_STR_HANDOFF, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_url(lambda url: url.startswith(STR_PREFIX), timeout=timeout_s * 1000)
            page.wait_for_load_state("domcontentloaded")
            log.info("Wyndham: on CoStar STR%s", "" if attempt == 1 else f" (attempt {attempt})")
            return page
        except Exception as exc:  # noqa: BLE001 - a stalled hand-off is retried once
            log_stall(page.context, f"CoStar hand-off from Wyndham (attempt {attempt} of {HANDOFF_ATTEMPTS}): {exc}")
    msg = f"CoStar did not open STR from Wyndham after {HANDOFF_ATTEMPTS} attempts; page captures are in the log"
    raise LoginRejected(msg)


PORTALS = {"hilton_lobby": hilton_lobby, "wyndham_community": wyndham_community}
