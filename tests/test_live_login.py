"""Live end-to-end integration test for CMP authentication and dashboard navigation.

Skipped automatically if .env is not present.
"""

from __future__ import annotations

import logging

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from cmp_auth import authenticate_cmp
from config import PROJECT_ROOT, load_settings
from dashboard_monitor import DashboardState, classify_state, navigate_to_dashboard
from imap_client import ImapClient, SystemClock
from network_diag import run_network_diagnostic

log = logging.getLogger(__name__)


@pytest.mark.live
def test_live_login_to_dashboard():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env file not present in project root")

    settings = load_settings(env_file=env_path)

    # Perform read-only diagnostic check prior to live attempt
    network_report = run_network_diagnostic(settings=settings)
    log.info(
        "Live test network diagnosis: position=%s, ready=%s",
        network_report.position.value,
        network_report.ready_for_live_test,
    )

    clock = SystemClock()
    imap_client = ImapClient(settings, clock)
    imap_client.connect()

    browser = None
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, bool | str] = {"headless": True}
            if settings.firefox_executable_path is not None:
                launch_options["executable_path"] = str(settings.firefox_executable_path)
            browser = playwright.firefox.launch(**launch_options)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            # Run full authentication
            authenticated = authenticate_cmp(settings=settings, otp_provider=imap_client, page=page, clock=clock)
            assert authenticated is True

            # Navigate to dashboard via real SPA menu click
            assert navigate_to_dashboard(page, settings, clock) is True
            assert classify_state(page) == DashboardState.DASHBOARD
    finally:
        if browser is not None:
            try:
                browser.close()
            except (PlaywrightError, OSError):
                log.debug("Live browser cleanup failed")
        try:
            imap_client.disconnect()
        except (OSError, RuntimeError):
            log.debug("IMAP cleanup failed")
