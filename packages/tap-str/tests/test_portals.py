"""Hilton portal hand-off against a local imitation of OnQ My Applications and CoStar."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from singer_playwright.login import LoginRejected
from singer_playwright.sso_login import SsoCredentials

from tap_str import portals

CREDS = SsoCredentials("gm01", "pw", "JBSWY3DPEHPK3PXP")


def _site(mode: dict[str, str]) -> type[BaseHTTPRequestHandler]:
    """mode['validate']: 'forward' or 'stall'. mode['str']: 'ok' or 'bounce' (back to validate)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _html(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body>{body}</body></html>".encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/myapps"):
                self._html('<a href="/validate" target="_blank">CoStar Benchmark</a>')
            elif self.path.startswith("/validate"):
                if mode["validate"] == "forward":
                    self._html('<script>location="/app/str"</script>')
                else:
                    self._html("Validating your STR registration... <button>Help</button>")
            elif self.path.startswith("/app/str"):
                if mode["str"] == "ok":
                    self._html("<h1>STR</h1>")
                else:
                    self._html('<script>location="/validate"</script>')
            else:
                self._html("")

    return Handler


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    mode = {"validate": "forward", "str": "ok"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _site(mode))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(portals, "HILTON_MY_APPS", f"{base}/myapps")
    monkeypatch.setattr(portals, "HILTON_MY_APPS_PREFIX", f"{base}/myapps")
    monkeypatch.setattr(portals, "STR_PREFIX", f"{base}/app/str")
    monkeypatch.setattr(portals, "STR_HOME", f"{base}/app/str")
    monkeypatch.setattr(portals, "HANDOFF_WAIT_S", 3)
    monkeypatch.setattr(portals, "DIRECT_WAIT_S", 3)
    yield mode
    server.shutdown()


@pytest.fixture
def page() -> Iterator:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium unavailable: {exc}")
        yield browser.new_context().new_page()
        browser.close()


def test_normal_hand_off_reaches_str(site, page, caplog) -> None:
    caplog.set_level(logging.WARNING)
    tab = portals.hilton_lobby(page, CREDS, timeout_s=10)
    assert tab.url.endswith("/app/str")
    assert "stalled" not in caplog.text


def test_stalled_hand_off_opens_str_directly(site, page, caplog) -> None:
    site["validate"] = "stall"
    caplog.set_level(logging.WARNING)
    tab = portals.hilton_lobby(page, CREDS, timeout_s=10)
    assert tab.url.endswith("/app/str")
    assert "hand-off stalled" in caplog.text
    assert portals.SCREENSHOT_MARKER not in caplog.text  # recovered: no capture needed


def test_persistent_stall_retries_once_then_fails_with_captures(site, page, caplog) -> None:
    site["validate"], site["str"] = "stall", "bounce"
    caplog.set_level(logging.WARNING)
    with pytest.raises(LoginRejected, match="after 2 attempts"):
        portals.hilton_lobby(page, CREDS, timeout_s=10)
    assert caplog.text.count("CoStar did not open STR (attempt") == 2
    assert "Validating your STR registration" in caplog.text
    assert "'Help'" in caplog.text
    assert portals.SCREENSHOT_MARKER in caplog.text
