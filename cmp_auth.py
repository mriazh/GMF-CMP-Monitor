"""CMP authentication module using Firefox Playwright.

Handles CAS login flow with username/password/OTP authentication.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from config import Settings

log = logging.getLogger(__name__)


class Clock(Protocol):
    """Protocol for time-keeping to allow test injection."""
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real system clock."""
    def now(self) -> float:
        return time.time()
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class OtpProvider(Protocol):
    """Protocol for OTP providers hooking into IMAP."""
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll_for_otp(self, run_start: datetime) -> str: ...


class AuthenticationError(Exception):
    """Raised for authentication failures with classified error messages."""


def authenticate_cmp(
    settings: Settings,
    otp_provider: OtpProvider,
    page: object,
    clock: Clock | None = None,
) -> bool:
    """Authenticate to the CMP CAS login page.

    Returns True if authentication succeeded.
    Raises AuthenticationError on failure.
    """
    if clock is None:
        clock = SystemClock()

    log.info("Starting CMP authentication")

    try:
        return _do_authenticate(settings, otp_provider, page, clock)
    except AuthenticationError:
        raise
    except Exception as exc:
        # Do not log raw exception (may contain sensitive data)
        log.error("Authentication failure occurred")
        raise AuthenticationError("Authentication failed") from exc


def _do_authenticate(
    settings: Settings,
    otp_provider: OtpProvider,
    page: object,
    clock: Clock,
) -> bool:
    # Navigate to CAS URL - log safe label only
    log.info("Navigating to CAS login page")
    page.goto(settings.cas_url, timeout=settings.navigation_timeout_ms)

    # Set default timeout for page operations
    page.set_default_timeout(settings.browser_timeout_ms)

    # Read execution token from hidden field at runtime
    try:
        execution = page.input_value("input[name='execution']")
        if execution:
            log.debug("Extracted execution token from page")
    except Exception:
        log.debug("Could not extract execution token")

    # Record login attempt timestamp using injected clock and configured timezone
    run_start_tz = ZoneInfo(settings.run_start_timezone)
    login_start = datetime.fromtimestamp(clock.now(), tz=run_start_tz)
    log.info("Login attempt at %s", login_start.isoformat())

    # Fill credentials
    page.fill("#username", settings.cmp_username.get_secret_value())
    page.fill("#password", settings.cmp_password.get_secret_value())
    log.info("Credentials filled")

    # Submit username/password form - use correct selector for initial form
    # The initial CAS login form (#fm1) uses a submit button with name="submit"
    page.click("#fm1 input[name='submit'][type='submit']")
    log.info("Initial form submitted")

    # Wait for OTP form to appear
    page.wait_for_selector("#token", timeout=settings.otp_form_timeout_ms)
    log.info("OTP form appeared")

    # Get OTP via IMAP
    log.info("Requesting OTP via IMAP...")
    otp_value = otp_provider.poll_for_otp(login_start)
    log.info("OTP received")

    # Submit OTP - OTP form (#login) uses _eventId_submit
    page.fill("#token", otp_value)
    page.click("#login input[name='_eventId_submit'][type='submit']")
    log.info("OTP submitted")

    # Wait for successful navigation to products page
    # The portal uses fragment URL: https://ep.iotcc.telkomsel.com/#!products
    # We need to wait for the fragment to contain #!products
    _wait_for_products_page(page, settings, clock)
    log.info("Authentication successful - reached products page")

    return True


def _wait_for_products_page(page: object, settings: Settings, clock: Clock) -> None:
    """Wait for navigation to the products page with fragment #!products on approved host."""
    deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)

    while clock.now() < deadline:
        try:
            url = page.url
            if url and _is_products_page(url):
                return
        except Exception:
            pass
        clock.sleep(0.1)

    raise AuthenticationError("Navigation to products page timed out")


def _is_products_page(url: str) -> bool:
    """Check if URL is the products page on approved host with correct fragment."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # Must be HTTPS
        if parsed.scheme != "https":
            return False
        # Must be on approved host
        if parsed.netloc != "ep.iotcc.telkomsel.com":
            return False
        # Must have fragment #!products
        if parsed.fragment != "!products":
            return False
        return True
    except Exception:
        return False