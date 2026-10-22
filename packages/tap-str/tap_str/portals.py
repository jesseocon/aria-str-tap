"""Brand portal entry: sign in through the brand's SSO and land on CoStar STR.

Each portal returns the browser tab that is showing product.costar.com/app/str.
Codes for the identity provider come from the TOTP secret, never from a push.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from singer_playwright.sso_login import SsoCredentials, complete_sso

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

STR_PREFIX = "https://product.costar.com/app/str"
# Lobby's "My Apps" link: IdP-initiated SSO straight into OnQ Insider's My Applications.
HILTON_MY_APPS = (
    "https://fd.hilton.com/idp/startSSO.ping?PartnerSpId=onqinsider"
    "&TargetResource=%2fInsider%2fMyApp%2fMyApplication.aspx%3f"
)
HILTON_MY_APPS_PREFIX = "https://onqinsider.hilton.com/Insider/MyApp/MyApplication.aspx"
HILTON_COSTAR_LINK = "CoStar Benchmark"


def _on_str(page: Page) -> bool:
    return page.url.startswith(STR_PREFIX)


def hilton_lobby(page: Page, creds: SsoCredentials, *, timeout_s: float = 240) -> Page:
    """Hilton SSO -> OnQ My Applications -> CoStar Benchmark (opens a new tab) -> STR."""
    page.goto(HILTON_MY_APPS, wait_until="domcontentloaded", timeout=90_000)
    complete_sso(page, creds, done=lambda p: p.url.startswith(HILTON_MY_APPS_PREFIX), timeout_s=timeout_s)
    log.info("Hilton: on My Applications")

    link = page.get_by_text(HILTON_COSTAR_LINK, exact=True).first
    link.wait_for(state="visible", timeout=60_000)
    with page.context.expect_page(timeout=60_000) as opened:
        link.click()
    costar = opened.value
    costar.bring_to_front()
    # CoStar asks Hilton's IdP again (SAML); with the session above it usually passes straight through.
    complete_sso(costar, creds, done=_on_str, timeout_s=timeout_s)
    costar.wait_for_load_state("domcontentloaded")
    log.info("Hilton: on CoStar STR")
    return costar


PORTALS = {"hilton_lobby": hilton_lobby}
