"""CMP authentication module using Firefox Playwright.

Handles CAS login flow with username/password/OTP authentication.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse, ParseResult
from zoneinfo import ZoneInfo

from config import Settings, APPROVED_CMP_HOST

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

        # Set default timeout for page operations
        page.set_default_timeout(settings.browser_timeout_ms)

        current_step = "waiting for login form or portal redirect"
        auth_outcome = _bounded_wait_for_auth_form_or_redirect(page, settings, clock)

        if auth_outcome == "already_authenticated":
            log.info("CAS session still valid; skipping credential/OTP submission")
            _wait_for_products_page(page, settings, clock)
            return True

        # Record login attempt timestamp using injected clock and configured timezone
        current_step = "recording login attempt"
        run_start_tz = ZoneInfo(settings.run_start_timezone)
        login_start = datetime.fromtimestamp(clock.now(), tz=run_start_tz)
        log.info("Login attempt at %s", login_start.isoformat())

        if auth_outcome == "login_form":
            # Read execution token from hidden field at runtime
            current_step = "extracting execution token"
            try:
                execution = page.input_value("input[name='execution']")
                if execution:
                    log.debug("Extracted execution token from page")
            except Exception:
                log.debug("Could not extract execution token")

            # Fill credentials
            current_step = "filling credentials"
            page.fill("#username", settings.cmp_username.get_secret_value())
            page.fill("#password", settings.cmp_password.get_secret_value())
            log.info("Credentials filled")

            # Submit username/password form
            current_step = "submitting initial credentials form"
            page.wait_for_selector("#fm1 input[name='submit']", state="visible", timeout=settings.navigation_timeout_ms)
            _wait_for_initial_submit_enabled(page, settings)
            page.click("#fm1 input[name='submit'][type='submit']", timeout=settings.navigation_timeout_ms)
            log.info("Initial form submitted")

            # Wait for OTP form to appear
            current_step = "waiting for OTP form (#token)"
            page.wait_for_selector("#token", timeout=settings.otp_form_timeout_ms)
            log.info("OTP form appeared")
        elif auth_outcome == "otp_form":
            log.info("OTP form already present on CAS page")

        # Get OTP via IMAP
        current_step = "requesting OTP via IMAP"
        log.info("Requesting OTP via IMAP...")
        otp_value = otp_provider.poll_for_otp(login_start)
        log.info("OTP received")

        # Submit OTP
        current_step = "submitting OTP form"
        page.fill("#token", otp_value)
        page.click("#login input[name='_eventId_submit'][type='submit']", timeout=settings.navigation_timeout_ms)
        log.info("OTP submitted")

        # Wait for successful navigation to products page
        current_step = "waiting for products page"
        _wait_for_products_page(page, settings, clock, post_otp=True)
        log.info("Authentication successful - reached products page")

        return True

    except Exception as exc:
        log.error("Authentication failure during %s: %s", current_step, type(exc).__name__)
        if isinstance(exc, AuthenticationError):
            raise
        raise AuthenticationError("Authentication failed") from exc


# Selectors for the initial CAS login form (verified against saved login page).
INITIAL_SUBMIT_SELECTOR = "#fm1 input[name='submit'][type='submit']"
INITIAL_SUBMIT_VISIBLE_SELECTOR = "#fm1 input[name='submit']"
USERNAME_SELECTOR = "#username"
PASSWORD_SELECTOR = "#password"
TOKEN_SELECTOR = "#token"


def _wait_for_initial_submit_enabled(page: object, settings: Settings) -> None:
    """Wait (bounded) for the initial login submit button to become enabled.

    The CAS portal keeps the submit button disabled until its own client-side
    validation is satisfied. We never force-click or bypass the disabled state:
    the smallest standard interaction that can trigger the portal's validation
    is a blur/change keyboard event on the last filled field, so we send one
    and then poll the real enabled state.
    """
    deadline = time.monotonic() + (settings.navigation_timeout_ms / 1000.0)
    diagnostics_logged = False

    # Trigger the portal's normal form validation once after filling fields.
    # This is a standard keyboard interaction (Tab blur), not a submission and
    # not a JavaScript/force bypass of the disabled state.
    try:
        page.press(PASSWORD_SELECTOR, "Tab")
    except Exception:
        pass

    while True:
        try:
            if page.is_enabled(INITIAL_SUBMIT_SELECTOR):
                return
        except Exception:
            pass
        if not diagnostics_logged:
            _log_initial_submit_diagnostics(page)
            diagnostics_logged = True
        if time.monotonic() >= deadline:
            raise AuthenticationError("Initial login submit button remained disabled")
        time.sleep(0.2)


def _log_initial_submit_diagnostics(page: object) -> None:
    """Log safe diagnostics about the disabled initial submit control.

    Only non-secret facts are logged: disabled state, enabled state,
    document ready state, field presence, and value lengths (never values).
    """
    try:
        log.warning(
            "Initial submit control not enabled: disabled=%s, enabled=%s, ready=%s, "
            "username_present=%s, password_present=%s, username_len=%d, password_len=%d, form_present=%s",
            page.get_attribute(INITIAL_SUBMIT_SELECTOR, "disabled"),
            page.is_enabled(INITIAL_SUBMIT_SELECTOR),
            page.evaluate("document.readyState"),
            page.is_visible(USERNAME_SELECTOR),
            page.is_visible(PASSWORD_SELECTOR),
            len(page.input_value(USERNAME_SELECTOR)),
            len(page.input_value(PASSWORD_SELECTOR)),
            page.is_visible("#fm1"),
        )
    except Exception:
        log.warning("Initial submit control not enabled; could not gather safe diagnostics")


def _bounded_wait_for_auth_form_or_redirect(
    page: object, settings: Settings, clock: Clock
) -> str:
    """Wait for login form, OTP form, or post-login portal redirect after navigating to CAS URL.

    Returns:
    - "already_authenticated": portal redirected directly to products page or root portal
    - "login_form": initial username/password form is visible (#username AND #password)
    - "otp_form": OTP form (#token) is visible
    Raises AuthenticationError on timeout.
    """
    deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)
    while clock.now() < deadline:
        try:
            url = getattr(page, "url", "")
            if url and (_is_products_page(url) or _is_root_portal(url)):
                return "already_authenticated"
            try:
                fragment = page.evaluate("window.location.hash")
                href = page.evaluate("window.location.href")
                if fragment == "#!products" and _is_approved_products_href(href):
                    return "already_authenticated"
            except Exception:
                pass

            try:
                if hasattr(page, "is_visible"):
                    if page.is_visible(USERNAME_SELECTOR) and page.is_visible(PASSWORD_SELECTOR):
                        return "login_form"
                    if page.is_visible(TOKEN_SELECTOR):
                        return "otp_form"
            except Exception:
                pass
        except Exception:
            pass
        clock.sleep(0.1)
    raise AuthenticationError("Timed out waiting for login form or portal redirect")


def _wait_for_products_page(
    page: object, settings: Settings, clock: Clock, post_otp: bool = False
) -> None:
    """Wait for navigation to the products page with fragment #!products on approved host.

    After OTP submit, CAS redirects to the root portal URL (https://ep.iotcc.telkomsel.com/)
    which is a valid post-login state. We then explicitly navigate to the products page.

    When ``post_otp`` is True the page must transition AWAY from the CAS login/OTP
    forms. If CAS instead shows the login or OTP form again (the submitted OTP was
    rejected or the session expired), raise an accurate, sanitized AuthenticationError
    immediately instead of spinning until the generic navigation timeout. This avoids
    masking an OTP rejection as a misleading "portal timed out" error.
    """
    deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)

    # First, wait for either root portal or products page (both indicate successful login)
    while clock.now() < deadline:
        try:
            url = page.url
            if url:
                parsed = urlparse(url)
                if _is_products_page(url) or _is_root_portal(url):
                    break
                if post_otp and _login_or_otp_form_visible(page):
                    raise AuthenticationError("OTP rejected by portal or session expired")
            # Also check fragment via JavaScript (Vaadin may update hash before page.url)
            try:
                fragment = page.evaluate("window.location.hash")
                href = page.evaluate("window.location.href")
                if fragment == "#!products" and _is_approved_products_href(href):
                    break
            except Exception:
                pass
        except AuthenticationError:
            raise
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
                if post_otp and _login_or_otp_form_visible(page):
                    raise AuthenticationError("OTP rejected by portal or session expired")
                # Also check fragment via JavaScript
                try:
                    fragment = page.evaluate("window.location.hash")
                    href = page.evaluate("window.location.href")
                    if fragment == "#!products" and _is_approved_products_href(href):
                        return
                except Exception:
                    pass
            except AuthenticationError:
                raise
            except Exception:
                pass
            clock.sleep(0.1)
        raise AuthenticationError("Navigation to products page timed out after portal")


def _is_approved_origin(parsed: ParseResult) -> bool:
    """True if parsed URL matches the approved HTTPS origin strictly.

    Rejects non-HTTPS schemes, foreign hostnames, explicit ports (including empty port syntax host:),
    and embedded credentials. Handles malformed ports safely.
    """
    try:
        return (
            parsed.scheme == "https"
            and parsed.hostname == APPROVED_CMP_HOST
            and parsed.netloc == APPROVED_CMP_HOST
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
        )
    except Exception:
        return False


def _is_approved_products_href(href: str) -> bool:
    """Check if href is an approved HTTPS URL with fragment #!products."""
    if not href:
        return False
    try:
        parsed = urlparse(href)
        return _is_approved_origin(parsed) and parsed.fragment == "!products"
    except Exception:
        return False


def _is_products_page(url: str) -> bool:
    """Check if URL is the products page on approved host with correct fragment."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return _is_approved_origin(parsed) and parsed.fragment == "!products"
    except Exception:
        return False


def _is_root_portal(url: str) -> bool:
    """Check if URL is the root portal on approved host (valid post-login state)."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return (
            _is_approved_origin(parsed)
            and parsed.path in ("", "/")
            and not parsed.fragment
        )
    except Exception:
        return False


def _is_cas_login_url(url: str) -> bool:
    """True if url is the CAS login page on the approved host.

    After an OTP submit the portal must leave the CAS login flow behind; a return
    to this URL is the unambiguous signal that the submitted OTP was rejected or
    the session expired.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return _is_approved_origin(parsed) and parsed.path.startswith("/cas/login")
    except Exception:
        return False


def _login_or_otp_form_visible(page: object) -> bool:
    """Return True if the portal is showing the CAS login / OTP form again.

    After a successful OTP submit the portal must leave these forms behind. A
    return to the CAS login URL with the username field (or, on that URL, the OTP
    field) visible means the submitted OTP was rejected or the session expired.

    The check is scoped to the CAS login URL because a Vaadin SPA can leave an OTP
    field's visibility flag set after navigation; only a return to the login page
    is a reliable rejection signal.
    """
    try:
        if hasattr(page, "is_visible"):
            url = getattr(page, "url", "") or ""
            on_cas_login = _is_cas_login_url(url)
            if page.is_visible(USERNAME_SELECTOR):
                return True
            if on_cas_login and page.is_visible(TOKEN_SELECTOR):
                return True
    except Exception:
        pass
    return False
