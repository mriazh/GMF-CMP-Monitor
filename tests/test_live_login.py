"""Live end-to-end integration test for CMP authentication and dashboard navigation.
Skipped automatically if .env is not present.
"""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from config import load_settings, PROJECT_ROOT
from imap_client import ImapClient, SystemClock
from cmp_auth import authenticate_cmp
from dashboard_monitor import classify_state, DashboardState

@pytest.mark.live
def test_live_login_to_dashboard():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env file not present in project root")

    settings = load_settings(env_file=env_path)
    clock = SystemClock()
    imap_client = ImapClient(settings, clock)
    imap_client.connect()

    try:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(headless=settings.headless)
            context = browser.new_context()
            page = context.new_page()

            # Run full authentication
            authenticated = authenticate_cmp(settings=settings, otp_provider=imap_client, page=page, clock=clock)
            assert authenticated is True

            # Navigate to dashboard
            page.goto(settings.cmp_dashboard_url, timeout=settings.navigation_timeout_ms, wait_until="commit")
            assert classify_state(page) == DashboardState.DASHBOARD
    finally:
        imap_client.disconnect()
