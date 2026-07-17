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
        log.error("Authentication failure: %s", type(exc).__name__)
        raise AuthenticationError("Authentication failed") from exc


def _do_authenticate(
    settings: Settings,
    otp_provider: OtpProvider,
    page: object,
    clock: Clock,
) -> bool:
    current_step = "initializing"
    try:
        # Navigate to CAS URL - log safe label only
        current_step = "navigating to CAS login page"
        log.info("Navigating to CAS login page")
        page.goto(settings.cas_url, timeout=settings.navigation_timeout_ms, wait_until="domcontentloaded")
        page.wait_for_selector("#username", timeout=settings.navigation_timeout_ms)

        # Set default timeout for page operations
        page.set_default_timeout(settings.browser_timeout_ms)

        # Read execution token from hidden field at runtime
        current_step = "extracting execution token"
        try:
            execution = page.input_value("input[name='execution']")
            if execution:
                log.debug("Extracted execution token from page")
        except Exception:
            log.debug("Could not extract execution token")

        # Record login attempt timestamp using injected clock and configured timezone
        current_step = "recording login attempt"
        run_start_tz = ZoneInfo(settings.run_start_timezone)
        login_start = datetime.fromtimestamp(clock.now(), tz=run_start_tz)
        log.info("Login attempt at %s", login_start.isoformat())

        # Fill credentials
        current_step = "filling credentials"
        page.fill("#username", settings.cmp_username.get_secret_value())
        page.fill("#password", settings.cmp_password.get_secret_value())
        log.info("Credentials filled")

        # Submit username/password form
        current_step = "submitting initial credentials form"
        page.wait_for_selector("#fm1 input[name='submit']", state="visible", timeout=settings.navigation_timeout_ms)
        page.click("#fm1 input[name='submit'][type='submit']")
        log.info("Initial form submitted")

        # Wait for OTP form to appear
        current_step = "waiting for OTP form (#token)"
        page.wait_for_selector("#token", timeout=settings.otp_form_timeout_ms)
        log.info("OTP form appeared")

        # Get OTP via IMAP
        current_step = "requesting OTP via IMAP"
        log.info("Requesting OTP via IMAP...")
        otp_value = otp_provider.poll_for_otp(login_start)
        log.info("OTP received")

        # Submit OTP
        current_step = "submitting OTP form"
        page.fill("#token", otp_value)
        page.click("#login input[name='_eventId_submit'][type='submit']")
        log.info("OTP submitted")

        # Wait for successful navigation to products page
        current_step = "waiting for products page"
        _wait_for_products_page(page, settings, clock)
        log.info("Authentication successful - reached products page")

        return True

    except Exception as exc:
        log.error("Authentication failure during %s: %s", current_step, type(exc).__name__)
        if isinstance(exc, AuthenticationError):
            raise
        raise AuthenticationError("Authentication failed") from exc


def _wait_for_products_page(page: object, settings: Settings, clock: Clock) -> None:
    """Wait for navigation to the products page with fragment #!products on approved host.

    After OTP submit, CAS redirects to the root portal URL (https://ep.iotcc.telkomsel.com/)
    which is a valid post-login state. We then explicitly navigate to the products page.
    """
    deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)

    # First, wait for either root portal or products page (both indicate successful login)
    while clock.now() < deadline:
        try:
            url = page.url
            if url and (_is_products_page(url) or _is_root_portal(url)):
                break
            # Also check fragment via JavaScript (Vaadin may update hash before page.url)
            try:
                fragment = page.evaluate("window.location.hash")
                href = page.evaluate("window.location.href")
                if fragment == "#!products" and "ep.iotcc.telkomsel.com" in href:
                    break
            except Exception:
                pass
        except Exception:
            pass
        clock.sleep(0.1)
    else:
        raise AuthenticationError("Navigation to portal timed out")

    # If we landed on root portal, explicitly navigate to products page
    if _is_root_portal(page.url):
        log.info("Landed on root portal, navigating to products page")
        page.goto(
            settings.cmp_products_url,
            timeout=settings.navigation_timeout_ms,
            wait_until="domcontentloaded",
        )
        # Wait for products page fragment
        deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)
        while clock.now() < deadline:
            try:
                url = page.url
                if url and _is_products_page(url):
                    return
                # Also check fragment via JavaScript
                try:
                    fragment = page.evaluate("window.location.hash")
                    href = page.evaluate("window.location.href")
                    if fragment == "#!products" and "ep.iotcc.telkomsel.com" in href:
                        return
                except Exception:
                    pass
            except Exception:
                pass
            clock.sleep(0.1)
        raise AuthenticationError("Navigation to products page timed out after portal")


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


def _is_root_portal(url: str) -> bool:
    """Check if URL is the root portal on approved host (valid post-login state)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # Must be HTTPS
        if parsed.scheme != "https":
            return False
        # Must be on approved host
        if parsed.netloc != "ep.iotcc.telkomsel.com":
            return False
        # Must be root path (empty or /) with no fragment
        if parsed.path not in ("", "/"):
            return False
        if parsed.fragment:
            return False
        return True
    except Exception:
        return False
