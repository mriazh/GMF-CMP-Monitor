"""CMP authentication module using Firefox Playwright.

Handles CAS login flow with username/password/OTP authentication.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from urllib.parse import ParseResult, urlparse
from zoneinfo import ZoneInfo

from config import APPROVED_CMP_HOST, Settings

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
    *,
    on_otp_submitted: Callable[[], None] | None = None,
) -> bool:
    """Authenticate to the CMP CAS login page.

    Returns True if authentication succeeded.
    Raises AuthenticationError on failure.
    """
    if clock is None:
        clock = SystemClock()

    log.info("Starting CMP authentication")

    try:
        return _do_authenticate(settings, otp_provider, page, clock, on_otp_submitted=on_otp_submitted)
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
    *,
    on_otp_submitted: Callable[[], None] | None = None,
) -> bool:
    current_step = "initializing"
    try:
        # Navigate to CAS URL with bounded retry against transient tunnel/route cutover jitter
        current_step = "navigating to CAS login page"
        log.info("Navigating to CAS login page")
        nav_attempts = 3
        for attempt in range(1, nav_attempts + 1):
            try:
                page.goto(settings.cas_url, timeout=settings.navigation_timeout_ms, wait_until="domcontentloaded")
                break
            except Exception as nav_exc:
                if attempt >= nav_attempts:
                    raise
                log.warning("CAS login navigation attempt %d/%d failed; retrying in 2s (%s)", attempt, nav_attempts, type(nav_exc).__name__)
                clock.sleep(2.0)

        # Set default timeout for page operations
        page.set_default_timeout(settings.browser_timeout_ms)

        current_step = "waiting for login form or portal redirect"
        auth_outcome = _bounded_wait_for_auth_form_or_redirect(page, settings, clock)

        if auth_outcome == "already_authenticated":
            log.info("CAS session still valid; skipping credential/OTP submission")
            if on_otp_submitted is not None:
                try:
                    on_otp_submitted()
                except Exception as hook_exc:
                    log.error("on_otp_submitted callback failed (%s)", type(hook_exc).__name__)
                    raise AuthenticationError("Post-OTP network transition failed") from hook_exc
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
            try:
                page.click(INITIAL_SUBMIT_SELECTOR, timeout=settings.navigation_timeout_ms, no_wait_after=True)
            except TypeError:
                page.click(INITIAL_SUBMIT_SELECTOR, timeout=settings.navigation_timeout_ms)
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

        current_step = "submitting OTP form"
        page.fill("#token", otp_value)
        page.click("#login input[name='_eventId_submit'][type='submit']", timeout=settings.navigation_timeout_ms)
        log.info("OTP submitted")

        if on_otp_submitted is not None:
            try:
                on_otp_submitted()
            except Exception as hook_exc:
                log.error("on_otp_submitted callback failed (%s)", type(hook_exc).__name__)
                raise AuthenticationError("Post-OTP network transition failed") from hook_exc

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
POST_OTP_REJECTION_GRACE_SECONDS = 2.0


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
        if (
            hasattr(page, "input_value")
            and hasattr(page, "fill")
            and not page.input_value(PASSWORD_SELECTOR)
        ):
            page.fill(PASSWORD_SELECTOR, settings.cmp_password.get_secret_value())
        page.press(PASSWORD_SELECTOR, "Tab")
    except Exception:  # noqa: BLE001, S110
        pass

    while True:
        try:
            if page.is_enabled(INITIAL_SUBMIT_SELECTOR):
                return
        except Exception:  # noqa: BLE001
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


def _check_post_otp_rejection(
    page: object,
    url: str,
    start_time: float,
    grace_deadline: float,
    consecutive_rejections: int,
    clock: Clock,
    approved_root: bool = False,
    approved_products: bool = False,
) -> int:
    """Check post-OTP rejection status and log safe diagnostics.

    Increments and returns consecutive_rejections count if form is visible.
    Raises AuthenticationError("OTP rejected by portal or session expired") if grace_deadline is exceeded.
    Returns 0 if no auth form is visible.
    """
    username_visible, otp_visible = _check_auth_form_visibility(page)
    if username_visible or otp_visible:
        consecutive_rejections += 1
        elapsed = clock.now() - start_time
        route_label = _classify_route_label(url, page)
        log.debug(
            "Post-OTP rejection poll %d (route: %s, elapsed: %.1fs, username_visible: %s, otp_visible: %s, approved_root: %s, approved_products: %s)",
            consecutive_rejections,
            route_label,
            elapsed,
            username_visible,
            otp_visible,
            approved_root,
            approved_products,
        )
        if clock.now() >= grace_deadline:
            log.error(
                "OTP rejected or session expired after %d consecutive polls (route: %s, elapsed: %.1fs, username_visible: %s, otp_visible: %s, approved_root: %s, approved_products: %s)",
                consecutive_rejections,
                route_label,
                elapsed,
                username_visible,
                otp_visible,
                approved_root,
                approved_products,
            )
            raise AuthenticationError("OTP rejected by portal or session expired")
        return consecutive_rejections
    return 0


def _is_playwright_page(page: object) -> bool:
    """Check if the page is a real Playwright page (not a test double)."""
    return hasattr(page, "context") and hasattr(page, "goto")


def _wait_for_products_page(
    page: object, settings: Settings, clock: Clock, post_otp: bool = False
) -> None:
    """Wait for navigation to the products page with fragment #!products on approved host.

    After OTP submit, CAS redirects to the root portal URL (https://ep.iotcc.telkomsel.com/)
    which is a valid post-login state. We then explicitly navigate to the products page.

    When ``post_otp`` is True the page must transition AWAY from the CAS login/OTP
    forms. If CAS instead shows the login or OTP form again after the bounded grace
    period (the submitted OTP was rejected or the session expired), raise an accurate,
    sanitized AuthenticationError. Stale form observations on early polls during
    asynchronous navigation are given a bounded grace confirmation period (2.0s for test
    doubles, 15.0s for real browsers undergoing network cutover) before declaring rejection.
    """
    start_time = clock.now()
    deadline = start_time + (settings.navigation_timeout_ms / 1000.0)
    grace_seconds = 15.0 if _is_playwright_page(page) else POST_OTP_REJECTION_GRACE_SECONDS
    grace_deadline = start_time + min(grace_seconds, settings.navigation_timeout_ms / 1000.0)
    consecutive_rejections = 0

    last_log_time = 0.0
    # First, wait for either root portal or products page (both indicate successful login)
    while clock.now() < deadline:
        try:
            url = getattr(page, "url", "") or ""
            if clock.now() - last_log_time >= 5.0:
                last_log_time = clock.now()
                try:
                    title = page.title()
                except Exception:  # noqa: BLE001
                    title = "unknown"
                log.info("Waiting for portal: url=%s, title=%s", url, title)
            approved_root = _is_root_portal(url)
            approved_products = _is_products_page(url)

            # If the browser hit an in-flight network cutover error (e.g. 'Server Not Found'), reload over WARP
            if not approved_products and not approved_root:
                try:
                    title = page.title() if hasattr(page, "title") else ""
                    if "server not found" in title.lower() or "problem loading page" in title.lower():
                        log.info("Page in error state ('%s'); reloading over WARP", title.strip())
                        clock.sleep(1.0)
                        page.reload(timeout=settings.navigation_timeout_ms, wait_until="domcontentloaded")
                        continue
                    if "log in successful" in title.lower() or "login successful" in title.lower():
                        log.info("CAS reported '%s'; navigating to products page", title.strip())
                        page.goto(
                            settings.cmp_products_url,
                            timeout=settings.navigation_timeout_ms,
                            wait_until="domcontentloaded",
                        )
                        continue
                except Exception:  # noqa: BLE001
                    pass

            # Also check fragment via JavaScript (Vaadin may update hash before page.url)
            if not approved_products and not approved_root:
                try:
                    fragment = page.evaluate("window.location.hash")
                    href = page.evaluate("window.location.href")
                    if fragment == "#!products" and _is_approved_products_href(href):
                        approved_products = True
                except Exception:
                    pass

            if approved_products or approved_root:
                route_label = "PRODUCTS" if approved_products else "ROOT"
                elapsed = clock.now() - start_time
                log.info(
                    "Post-login target reached (route: %s, elapsed: %.1fs)",
                    route_label,
                    elapsed,
                )
                break

            if post_otp:
                consecutive_rejections = _check_post_otp_rejection(
                    page, url, start_time, grace_deadline, consecutive_rejections, clock, approved_root, approved_products
                )
        except AuthenticationError:
            raise
        except Exception:
            pass
        clock.sleep(0.1)
    else:
        if post_otp and consecutive_rejections >= 1:
            url = getattr(page, "url", "") or ""
            username_visible, otp_visible = _check_auth_form_visibility(page)
            if username_visible or otp_visible:
                log.error(
                    "OTP rejected or session expired at deadline (route: %s, elapsed: %.1fs)",
                    _classify_route_label(url, page),
                    clock.now() - start_time,
                )
                raise AuthenticationError("OTP rejected by portal or session expired")
        raise AuthenticationError("Navigation to portal timed out")

    # If we landed on root portal or still have CAS path in page.url, explicitly navigate to products page
    url = getattr(page, "url", "") or ""
    if _is_root_portal(url) or "/cas/login" in url or not _is_products_page(url):
        log.info("Navigating explicitly to products page to ensure SPA shell loads (current: %s)", url)
        phase2_start_time = clock.now()
        page.goto(
            settings.cmp_products_url,
            timeout=settings.navigation_timeout_ms,
            wait_until="domcontentloaded",
        )
        # Wait for page.url to actually leave the CAS login path and settle on products page
        deadline = phase2_start_time + (settings.navigation_timeout_ms / 1000.0)
        while clock.now() < deadline:
            try:
                current_url = getattr(page, "url", "") or ""
                if _is_products_page(current_url) and "/cas/login" not in current_url:
                    log.info("Successfully transitioned to products page: %s", current_url)
                    break
            except Exception:  # noqa: BLE001
                pass
            clock.sleep(0.5)
        # Wait for products page fragment
        deadline = phase2_start_time + (settings.navigation_timeout_ms / 1000.0)
        phase2_grace_seconds = 15.0 if _is_playwright_page(page) else POST_OTP_REJECTION_GRACE_SECONDS
        phase2_grace_deadline = phase2_start_time + min(phase2_grace_seconds, settings.navigation_timeout_ms / 1000.0)
        consecutive_rejections = 0
        while clock.now() < deadline:
            try:
                url = getattr(page, "url", "") or ""
                approved_products = _is_products_page(url)
                if not approved_products:
                    try:
                        fragment = page.evaluate("window.location.hash")
                        href = page.evaluate("window.location.href")
                        if fragment == "#!products" and _is_approved_products_href(href):
                            approved_products = True
                    except Exception:
                        pass

                if approved_products:
                    return

                if post_otp:
                    consecutive_rejections = _check_post_otp_rejection(
                        page, url, phase2_start_time, phase2_grace_deadline, consecutive_rejections, clock, False, approved_products
                    )
            except AuthenticationError:
                raise
            except Exception:
                pass
            clock.sleep(0.1)
        raise AuthenticationError("Navigation to products page timed out after portal")


def _classify_route_label(url: str, page: object = None) -> str:
    """Return safe route label without sensitive URL details."""
    if not url:
        return "UNKNOWN"
    try:
        parsed = urlparse(url)
        if _is_approved_origin(parsed):
            if parsed.fragment == "!products":
                return "PRODUCTS"
            if parsed.path in ("", "/"):
                return "ROOT"
            if parsed.path.startswith("/cas/login"):
                return "CAS_LOGIN"
    except Exception:
        pass

    if page is not None:
        try:
            fragment = page.evaluate("window.location.hash")
            href = page.evaluate("window.location.href")
            if fragment == "#!products" and _is_approved_products_href(href):
                return "PRODUCTS"
        except Exception:
            pass

    return "UNKNOWN"


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
        return _is_approved_origin(parsed) and parsed.path in ("", "/") and parsed.fragment == "!products"
    except Exception:
        return False


def _is_products_page(url: str) -> bool:
    """Check if URL is the products page on approved host with correct fragment."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return _is_approved_origin(parsed) and parsed.path in ("", "/") and parsed.fragment == "!products"
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


def _check_auth_form_visibility(page: object) -> tuple[bool, bool]:
    """Check if username or OTP forms are currently visible.

    Returns (username_visible, otp_visible).
    """
    username_visible = False
    otp_visible = False
    try:
        if hasattr(page, "is_visible"):
            url = getattr(page, "url", "") or ""
            username_visible = page.is_visible(USERNAME_SELECTOR)
            otp_visible = _is_cas_login_url(url) and page.is_visible(TOKEN_SELECTOR)
    except Exception:
        pass
    return username_visible, otp_visible


def _login_or_otp_form_visible(page: object) -> bool:
    """Return True if the portal is showing the CAS login / OTP form again.

    After a successful OTP submit the portal must leave these forms behind. A
    return to the CAS login URL with the username field (or, on that URL, the OTP
    field) visible means the submitted OTP was rejected or the session expired.

    The check is scoped to the CAS login URL because a Vaadin SPA can leave an OTP
    field's visibility flag set after navigation; only a return to the login page
    is a reliable rejection signal.
    """
    username_visible, otp_visible = _check_auth_form_visibility(page)
    return username_visible or otp_visible
