"""CMP Dashboard monitoring module.

Monitors the Telkomsel CMP dashboard for GMF, periodically refreshing
and handling session expiry.

All dashboard navigation is performed by clicking the real Vaadin SPA
menu item. Direct navigation to ``#!dashboard`` is NEVER used: a direct
``page.goto`` changes ``page.url`` without actually rendering the
dashboard view, which produces a false-positive dashboard detection
(the Vaadin router later resets the hash to ``#!products``).
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Protocol
from urllib.parse import urlparse, ParseResult

from config import Settings, APPROVED_CMP_HOST
from cmp_auth import authenticate_cmp, AuthenticationError
from imap_client import OtpProviderProtocol

log = logging.getLogger(__name__)

# SPA menu selectors for the Vaadin 7 dashboard navigation.
DASHBOARD_MENU_SELECTOR = "span.main-menu-item-caption"
DASHBOARD_MENU_TEXT = "Dashboard"
# Selected-menu item and view DOM signals. These selectors are marked
# UNVERIFIED until confirmed against the real post-click DOM during a live
# session (see _log_dashboard_dom_diagnostics for safe selector facts).
# Note: Vaadin may place the .selected class on the parent div container,
# not the child caption span.
DASHBOARD_SELECTED_SELECTOR = "div.main-menu-item.selected"
DASHBOARD_VIEW_SELECTOR = "div.dashboard-view"

# Poll interval (seconds) for state/menu/verification polls.
POLL_INTERVAL_SECONDS = 1.0
# Number of consecutive verified polls required before a dashboard is accepted.
VERIFIED_DASHBOARD_POLLS = 2
# Bounded observation period (seconds) for a dashboard URL whose UI is not
# verified before staging recovery through the products URL.
INCONSISTENT_DASHBOARD_OBSERVE_SECONDS = 3.0
# Bounded route-settling period (seconds) when AUTH_EXPIRED is detected
# to allow false session-closed or temporary CAS redirects to return to Products.
AUTH_SETTLE_SECONDS = 5.0


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


class DashboardState(Enum):
    """Recognized states on the CMP dashboard."""
    DASHBOARD = auto()
    PRODUCTS = auto()
    AUTH_EXPIRED = auto()
    UNKNOWN = auto()


class RecoveryExhaustedError(Exception):
    """Raised when recovery attempts are exhausted."""
    pass


class RecoveryError(Exception):
    """Safe exception for recovery failures that sanitizes sensitive data."""
    pass


class AuthenticationRequiredError(RecoveryError):
    """Raised when the page needs re-authentication during dashboard navigation.

    Subclasses RecoveryError so generic recovery handlers still treat it as a
    navigation failure, while navigation callers can trigger a fresh login.
    """
    pass


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


def _live_fragment(page: object, parsed: ParseResult) -> str:
    """Return the SPA route fragment, preferring the live ``window.location.hash``.

    Playwright Firefox's ``page.url`` can lag behind ``window.location.hash``
    mutations during a Vaadin route transition: the browser frame URL may still
    report ``#!products`` while the live hash has already changed to
    ``#!dashboard`` and the dashboard view has rendered. The live hash is the
    authoritative route signal for the SPA router; ``page.url`` remains
    authoritative only for origin validation. Falls back to the parsed URL
    fragment ONLY for test doubles or objects that genuinely do not provide
    ``evaluate``. If ``evaluate`` is available, its result is authoritative:
    empty string, malformed/non-string result, or evaluation exception all
    produce a non-Dashboard fragment (empty string) rather than falling back
    to the stale ``page.url``.
    """
    if hasattr(page, "evaluate"):
        try:
            hash_value = page.evaluate("window.location.hash")
        except Exception:
            return ""
        if isinstance(hash_value, str) and hash_value.startswith("#"):
            return hash_value[1:]
        return ""
    return parsed.fragment


def classify_state(page: object) -> DashboardState:
    """Classify current page state based on URL and live SPA hash.

    Returns DashboardState enum based on URL parsing:
    - DASHBOARD: HTTPS, approved host, fragment #!dashboard
    - PRODUCTS: HTTPS, approved host, fragment #!products
    - AUTH_EXPIRED: HTTPS, approved host, exact auth paths (/cas/login, /cas/login/, /session-closed, /session-closed/)
    - UNKNOWN: anything else (including foreign-host lookalikes, HTTP, non-auth endpoints, non-default ports, embedded credentials, malformed ports)

    The route fragment is read from the live ``window.location.hash`` when
    available (authoritative for the Vaadin SPA router) and only falls back to
    the ``page.url`` fragment otherwise, so a stale Firefox ``page.url`` cannot
    misclassify a rendered dashboard as Products.
    """
    try:
        url = getattr(page, "url", "")
        if not url:
            return DashboardState.UNKNOWN
        parsed = urlparse(url)
        if not _is_approved_origin(parsed):
            return DashboardState.UNKNOWN

        # Check auth paths BEFORE checking fragments, so misleading fragments on auth paths cannot be classified as DASHBOARD or PRODUCTS
        if parsed.path in ("/cas/login", "/cas/login/", "/session-closed", "/session-closed/"):
            return DashboardState.AUTH_EXPIRED

        # Check fragment for dashboard/products (live hash preferred)
        fragment = _live_fragment(page, parsed)
        if fragment == "!dashboard":
            return DashboardState.DASHBOARD
        elif fragment == "!products":
            return DashboardState.PRODUCTS

        return DashboardState.UNKNOWN
    except Exception:
        return DashboardState.UNKNOWN


def _dashboard_dom_ready(page: object) -> bool:
    """Check the Vaadin DOM for a stable dashboard-ready signal.

    Looks for a selected Dashboard menu item or a dashboard-specific visible
    container. Any failure returns False; the URL alone is never trusted.
    """
    try:
        selected = page.locator(DASHBOARD_SELECTED_SELECTOR, has_text=DASHBOARD_MENU_TEXT).first
        if hasattr(selected, "is_visible") and selected.is_visible():
            return True
        container = page.locator(DASHBOARD_VIEW_SELECTOR).first
        if hasattr(container, "is_visible") and container.is_visible():
            return True
    except Exception:
        pass
    return False


def _is_verified_dashboard(page: object) -> bool:
    """True only when approved origin, live SPA hash and DOM confirm dashboard.

    This is the real dashboard verifier: it requires the approved HTTPS origin
    (from ``page.url``), the live ``window.location.hash == "#!dashboard"``
    (authoritative for the Vaadin SPA router; ``page.url`` can lag behind in
    Firefox during route transitions) and a dashboard-ready DOM signal.
    """
    try:
        url = getattr(page, "url", "")
        if not url:
            return False
        parsed = urlparse(url)
        if not _is_approved_origin(parsed):
            return False
        if _live_fragment(page, parsed) != "!dashboard":
            return False
        return _dashboard_dom_ready(page)
    except Exception:
        return False


def _log_dashboard_dom_diagnostics(page: object) -> None:
    """Emit safe DEBUG diagnostics about the dashboard menu and DOM.

    Reports only: the current normalized route state, whether
    ``window.location.hash`` equals ``#!dashboard``, whether the Dashboard
    caption exists, its class names, the tag/class names of a small number of
    ancestors, and whether candidate dashboard containers exist. Never logs
    HTML, page text beyond the literal menu label, credentials, cookies,
    execution tokens, or URLs containing query parameters.
    """
    if not log.isEnabledFor(logging.DEBUG):
        return
    try:
        try:
            route_state = classify_state(page).name
        except Exception:
            route_state = "?"
        hash_ok = False
        try:
            if hasattr(page, "evaluate"):
                hash_ok = page.evaluate("window.location.hash") == "#!dashboard"
        except Exception:
            hash_ok = False
        menu = page.locator(DASHBOARD_MENU_SELECTOR, has_text=DASHBOARD_MENU_TEXT).first
        exists = False
        classes = None
        ancestors = None
        if hasattr(menu, "is_visible"):
            exists = menu.is_visible()
        if exists and hasattr(menu, "get_attribute"):
            try:
                classes = menu.get_attribute("class")
            except Exception:
                classes = None
        if exists and hasattr(menu, "evaluate"):
            try:
                ancestors = menu.evaluate(
                    "el => { const chain = []; let n = el; "
                    "for (let i = 0; n && i < 4; i++, n = n.parentElement) { "
                    "const cls = (n.className || '').split(/\\s+/).filter(Boolean).join('.'); "
                    "chain.push(n.tagName + (cls ? '.' + cls : '')); } "
                    "return chain.join(' < '); }"
                )
            except Exception:
                ancestors = None
        selected_exists = False
        view_exists = False
        try:
            sel = page.locator(DASHBOARD_SELECTED_SELECTOR, has_text=DASHBOARD_MENU_TEXT).first
            if hasattr(sel, "is_visible"):
                selected_exists = sel.is_visible()
        except Exception:
            pass
        try:
            view = page.locator(DASHBOARD_VIEW_SELECTOR).first
            if hasattr(view, "is_visible"):
                view_exists = view.is_visible()
        except Exception:
            pass
        log.debug(
            "Dashboard DOM diagnostics: state=%s hash_ok=%s caption_exists=%s "
            "classes=%r ancestors=%r selected=%s view=%s",
            route_state, hash_ok, exists, classes, ancestors,
            selected_exists, view_exists,
        )
    except Exception:
        pass


def _visible_dashboard_menu(page: object) -> object | None:
    """Return the visible, exact-text Dashboard menu item locator or None."""
    try:
        menu = page.locator(DASHBOARD_MENU_SELECTOR, has_text=DASHBOARD_MENU_TEXT).first
        if not hasattr(menu, "is_visible") or not menu.is_visible():
            return None
        # Exact-text guard: the caption must end with "Dashboard" (allowing font icon prefixes like \ue900).
        if hasattr(menu, "inner_text"):
            text = menu.inner_text().strip()
            if not text.endswith(DASHBOARD_MENU_TEXT):
                return None
        return menu
    except Exception:
        return None


# Maximum allowed SPA bootstrap/load time before Dashboard menu polling begins.
# The Vaadin 7 shell may take up to ~90s to fully load after domcontentloaded.
VAADIN_BOOTSTRAP_TIMEOUT_SECONDS = 150.0


def _is_playwright_page(page: object) -> bool:
    """Check if the page is a real Playwright page (not a test double)."""
    return hasattr(page, "context") and hasattr(page, "goto")


def _wait_for_vaadin_loading(page: object, clock: Clock, timeout_seconds: float) -> None:
    """Wait for the Vaadin SPA menu to become visible before navigation polling.

    The Vaadin 7 shell loads asynchronously: the initial HTML (domcontentloaded)
    renders only a loading shell, and the real menu items are created by the
    widgetset JavaScript, which can take tens of seconds. This pre-wait polls for
    the Dashboard menu so that the bounded click/verify deadline is not consumed
    by the SPA bootstrap. Returns as soon as the menu is visible.

    Skips the pre-wait for test doubles (FakePage) that lack Playwright's
    ``context`` attribute, so unit tests are not slowed down.

    Raises RecoveryError if the menu never becomes visible within the timeout.
    """
    # Test doubles (FakePage) do not have the Dashboard menu; skip immediately.
    if not _is_playwright_page(page):
        return
    if _visible_dashboard_menu(page) is not None:
        return
    log.info(
        "Waiting up to %.0fs for Vaadin SPA menu to load", timeout_seconds
    )
    deadline = clock.now() + timeout_seconds
    while clock.now() < deadline:
        if _visible_dashboard_menu(page) is not None:
            log.info("Vaadin SPA menu became visible")
            return
        clock.sleep(POLL_INTERVAL_SECONDS)
    log.error(
        "Vaadin SPA menu never became visible after %.0fs", timeout_seconds
    )
    raise RecoveryError(
        "Dashboard navigation failed: Vaadin SPA menu never became visible"
    )


def _click_dashboard_menu(page: object, settings: Settings, clock: Clock) -> bool:
    """Wait for the Dashboard menu item, click it, and verify a stable dashboard.

    The menu item may render late (the Vaadin SPA shell loads asynchronously), so
    it is polled once per second until visible. The menu is then clicked and the
    route/UI verification (URL + hash + DOM) is polled.

    The click is only repeated with positive evidence of a completed bounce: the
    page first left Products and later returned to Products. The menu
    is never re-clicked merely because the Products URL remains unchanged while
    the first navigation is still loading.

    Raises AuthenticationRequiredError if the page moves to CAS login or
    /session-closed while waiting. Raises RecoveryError on timeout.
    """
    # Pre-wait: the Vaadin 7 SPA shell may take up to 90s to fully load and
    # render the menu items. Poll for the menu to appear (or the v-app-loading
    # indicator to disappear) before starting the main navigation deadline.
    _wait_for_vaadin_loading(page, clock, VAADIN_BOOTSTRAP_TIMEOUT_SECONDS)
    deadline = clock.now() + (settings.navigation_timeout_ms / 1000.0)
    clicked = False
    left_products_seen = False
    verified_polls = 0
    # Throttled diagnostics for post-click unverified Dashboard state
    last_diag_time = None
    diag_count = 0
    while clock.now() < deadline:
        state = classify_state(page)
        if state == DashboardState.AUTH_EXPIRED:
            raise AuthenticationRequiredError(
                "Authentication required during dashboard navigation"
            )
        if _is_verified_dashboard(page):
            verified_polls += 1
            if verified_polls >= VERIFIED_DASHBOARD_POLLS:
                log.info("Dashboard verified (URL, hash, DOM)")
                return True
            log.debug("Dashboard verified once; confirming")
            clock.sleep(POLL_INTERVAL_SECONDS)
            continue

        verified_polls = 0

        if clicked:
            # An in-flight click: wait for the route transition. Only re-click
            # with positive evidence of a completed bounce (the page left
            # Products and later returned).
            if state != DashboardState.PRODUCTS:
                # The route moved away from Products: the click was consumed.
                left_products_seen = True
                log.debug("Menu click in flight; route left products (state=%s)", state.name)
            else:
                can_retry = left_products_seen
                if not can_retry:
                    log.debug("Menu click in flight; waiting for route transition")
                else:
                    # Confirmed bounce: exactly one retry per observed round-trip.
                    # Reset ``clicked`` so the next poll attempts the real menu
                    # click again (the route left Products and returned).
                    left_products_seen = False
                    clicked = False
                    log.info("Confirmed bounce back to products; retrying Dashboard menu click")
            # Post-click verification pending: emit throttled diagnostics for
            # every route state (Products, unknown, or unverified Dashboard
            # URL). First failure logs immediately, then at most once per 10s.
            now = clock.now()
            if last_diag_time is None or now - last_diag_time >= 10.0:
                _log_dashboard_dom_diagnostics(page)
                last_diag_time = now
                diag_count += 1
                log.debug(
                    "Dashboard unverified after click (poll %d): state=%s",
                    diag_count, state.name,
                )
            clock.sleep(POLL_INTERVAL_SECONDS)
            continue

        menu = _visible_dashboard_menu(page)
        if menu is None:
            _log_dashboard_dom_diagnostics(page)
            log.debug("Dashboard menu not visible yet; waiting")
            clock.sleep(POLL_INTERVAL_SECONDS)
            continue

        log.info("Clicking SPA Dashboard menu item")
        try:
            menu.click()
            clicked = True
            left_products_seen = False
        except Exception:
            log.debug("Dashboard menu click failed; will retry")
        clock.sleep(POLL_INTERVAL_SECONDS)
    # Final diagnostic before timeout
    _log_dashboard_dom_diagnostics(page)
    log.error("Dashboard verification timed out after %.0fs", settings.navigation_timeout_ms / 1000.0)
    raise RecoveryError("Dashboard navigation failed: could not verify dashboard")


def navigate_to_dashboard(
    page: object, settings: Settings, clock: Clock | None = None
) -> bool:
    """Navigate to the dashboard via the real SPA menu item.

    NEVER navigates directly to #!dashboard. If the SPA shell is not loaded
    (unknown state), first navigate to the products URL to render the menu,
    then wait for and click the Dashboard menu item. Verifies a stable
    dashboard state (URL + hash + DOM across two consecutive polls) before
    returning.

    Raises AuthenticationRequiredError if re-authentication is needed.
    Raises RecoveryError if the dashboard cannot be verified.
    """
    if clock is None:
        clock = SystemClock()

    state = classify_state(page)
    if state == DashboardState.AUTH_EXPIRED:
        raise AuthenticationRequiredError(
            "Authentication required during dashboard navigation"
        )
    if state == DashboardState.UNKNOWN:
        log.info("Unknown state; navigating to products to load the SPA shell")
        try:
            page.goto(
                settings.cmp_products_url,
                timeout=settings.navigation_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as nav_exc:
            raise RecoveryError("Dashboard navigation failed") from nav_exc

    return _click_dashboard_menu(page, settings, clock)


class ContinuousMonitor:
    """Dashboard monitor that periodically refreshes and handles session expiry."""

    def __init__(
        self,
        settings: Settings,
        page: object,
        otp_provider: OtpProviderProtocol,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._page = page
        self._otp_provider = otp_provider
        self._clock = clock or SystemClock()
        self._consecutive_recoveries = 0

    def _recover_to_dashboard(self) -> bool:
        """Attempt to recover to dashboard with bounded retries.

        Returns True if recovery succeeded.
        Raises RecoveryExhaustedError if limit is reached.
        Raises RecoveryError for navigation failures.
        """
        self._consecutive_recoveries += 1
        self._check_recovery_limit()

        # Wait for backoff before recovery attempt
        backoff = self._settings.recovery_backoff_seconds
        log.info("Waiting %.0fs before recovery attempt (%d/%d)",
                 backoff, self._consecutive_recoveries, self._settings.recovery_retry_limit)
        self._clock.sleep(backoff)

        # Try to navigate back to dashboard via the real SPA menu click
        try:
            navigate_to_dashboard(self._page, self._settings, self._clock)
        except RecoveryError:
            # Navigation failed, counter retained, will be checked on next cycle
            raise
        except Exception as nav_exc:
            log.error("Navigation to dashboard failed during recovery: %s", type(nav_exc).__name__)
            raise RecoveryError("Dashboard recovery failed") from nav_exc

        # Successfully recovered to dashboard
        self._consecutive_recoveries = 0
        return True

    def monitor_once(self) -> bool:
        """Execute one monitoring cycle.

        Returns True if re-authentication was performed and succeeded, False otherwise.
        Raises RecoveryExhaustedError if recovery limit is reached.
        Raises RecoveryError for recovery navigation failures.
        """
        try:
            current_state = classify_state(self._page)
            log.info("Current state: %s", current_state)

            if current_state == DashboardState.DASHBOARD:
                return self._monitor_dashboard_state()
            if current_state == DashboardState.PRODUCTS:
                log.info("Redirected to products; navigating to dashboard")
                return self._handle_products_state()
            if current_state == DashboardState.AUTH_EXPIRED:
                log.info("Session expired detected; checking route settle")
                return self._handle_auth_expired()

            log.warning("Unknown state detected; initiating bounded recovery")
            return self._recover_to_dashboard()

        except RecoveryExhaustedError:
            # Already handled in _recover_to_dashboard or _check_recovery_limit
            raise
        except AuthenticationRequiredError:
            # Dashboard navigation hit a login/session-closed page: re-login.
            log.info("Authentication required during monitoring; re-authenticating")
            return self._relogin_and_navigate()
        except RecoveryError:
            # Recovery navigation failed, counter already incremented
            raise
        except Exception as exc:
            # Do not log raw exception (may contain sensitive data)
            log.error("Monitoring error occurred: %s", type(exc).__name__)
            self._consecutive_recoveries += 1
            self._check_recovery_limit()
            raise RecoveryError("Monitoring error occurred") from exc

    def _monitor_dashboard_state(self) -> bool:
        """Wait for the refresh interval while polling the state once per second.

        Steady-state verification does not trust the URL alone: every one-second
        poll must confirm the verified dashboard condition (URL + hash + DOM).
        If the URL reports dashboard but the UI verification fails for two
        consecutive polls, the state is treated as inconsistent and the monitor
        recovers through Products and the real Dashboard menu click. URL state
        and verified UI state are logged separately.
        """
        deadline = self._clock.now() + self._settings.refresh_interval_seconds
        log.info("Monitoring dashboard; next refresh in %.0fs", self._settings.refresh_interval_seconds)
        unverified_polls = 0
        while self._clock.now() < deadline:
            self._clock.sleep(POLL_INTERVAL_SECONDS)
            state = classify_state(self._page)
            if state == DashboardState.DASHBOARD:
                verified = _is_verified_dashboard(self._page)
                log.debug("Dashboard state: %s | verified UI: %s", state.name, verified)
                if verified:
                    unverified_polls = 0
                    continue
                unverified_polls += 1
                _log_dashboard_dom_diagnostics(self._page)
                log.warning(
                    "Dashboard URL but UI not verified (%d/%d consecutive polls)",
                    unverified_polls, VERIFIED_DASHBOARD_POLLS,
                )
                if unverified_polls >= VERIFIED_DASHBOARD_POLLS:
                    log.info("Dashboard UI inconsistent; starting inconsistent-dashboard recovery")
                    return self._handle_inconsistent_dashboard()
                continue
            if state == DashboardState.PRODUCTS:
                log.info("Dashboard changed to PRODUCTS; navigating back to dashboard")
                return self._handle_products_state()
            if state == DashboardState.AUTH_EXPIRED:
                log.info("Session expired while waiting; checking route settle")
                return self._handle_auth_expired()
            log.warning("Dashboard changed to unknown state; bounded recovery")
            return self._recover_to_dashboard()

        # Refresh interval expired while still on the dashboard: reload.
        return self._handle_refresh()

    def _handle_products_state(self) -> bool:
        """Navigate from products back to the verified dashboard via the SPA menu."""
        try:
            navigate_to_dashboard(self._page, self._settings, self._clock)
        except AuthenticationRequiredError:
            log.info("Authentication required during dashboard navigation; re-authenticating")
            return self._relogin_and_navigate()
        except RecoveryError:
            log.error("Navigation to dashboard failed; starting bounded recovery")
            return self._recover_to_dashboard()
        # Only reset the counter after a verified dashboard state.
        self._consecutive_recoveries = 0
        return False

    def _handle_inconsistent_dashboard(self) -> bool:
        """Recover from a dashboard URL whose UI is not verified.

        The URL reports dashboard but the real Vaadin UI does not confirm it.
        Observe for a short bounded period: if the real state settles on
        products or session expiry, the normal handlers are used. If the page
        remains on an unverified dashboard URL, the SPA shell is staged through
        the products URL and the real Dashboard menu item is clicked. Never
        navigates directly to #!dashboard.
        """
        observe_deadline = self._clock.now() + INCONSISTENT_DASHBOARD_OBSERVE_SECONDS
        log.info(
            "Dashboard URL inconsistent with UI; observing for %.0fs",
            INCONSISTENT_DASHBOARD_OBSERVE_SECONDS,
        )
        while self._clock.now() < observe_deadline:
            self._clock.sleep(POLL_INTERVAL_SECONDS)
            state = classify_state(self._page)
            if state == DashboardState.AUTH_EXPIRED:
                log.info("Session expired during dashboard verification; checking route settle")
                return self._handle_auth_expired()
            if state == DashboardState.PRODUCTS:
                log.info("Dashboard inconsistency resolved to products; menu-clicking dashboard")
                return self._handle_products_state()
            if state == DashboardState.DASHBOARD and _is_verified_dashboard(self._page):
                log.info("Dashboard UI verified during observation")
                self._consecutive_recoveries = 0
                return False
            _log_dashboard_dom_diagnostics(self._page)
            log.debug("Dashboard still unverified (state=%s)", state.name)

        # Observation period elapsed with the URL still reporting an unverified
        # dashboard: stage through the products URL to reload the SPA shell,
        # then click the real Dashboard menu item.
        log.info("Dashboard UI unverified after observation; staging via products")
        try:
            self._page.goto(
                self._settings.cmp_products_url,
                timeout=self._settings.navigation_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as nav_exc:
            log.error("Products staging navigation failed: %s", type(nav_exc).__name__)
            return self._recover_to_dashboard()
        try:
            navigate_to_dashboard(self._page, self._settings, self._clock)
        except AuthenticationRequiredError:
            log.info("Authentication required while staging to dashboard; re-authenticating")
            return self._relogin_and_navigate()
        except RecoveryError:
            log.error("Dashboard menu navigation failed after staging; bounded recovery")
            return self._recover_to_dashboard()
        self._consecutive_recoveries = 0
        return False

    def _handle_refresh(self) -> bool:
        """Reload the dashboard and handle the post-reload route transition."""
        try:
            self._page.reload()
        except Exception:
            log.error("Dashboard reload failed; starting bounded recovery")
            return self._recover_to_dashboard()

        log.info("Dashboard reloaded; waiting for route transition")
        try:
            settled = self._wait_for_route_settle()
        except RecoveryError:
            log.error("Route did not settle after reload; starting bounded recovery")
            return self._recover_to_dashboard()

        if settled == DashboardState.PRODUCTS:
            log.info("Portal reset to PRODUCTS after refresh; navigating back to dashboard")
            return self._handle_products_state()
        if settled == DashboardState.AUTH_EXPIRED:
            log.info("Session expired after refresh; checking route settle")
            return self._handle_auth_expired()
        if settled == DashboardState.DASHBOARD:
            log.info("Dashboard verified after refresh")
            self._consecutive_recoveries = 0
            return False
        log.warning("Unknown state after refresh; bounded recovery")
        return self._recover_to_dashboard()

    def _wait_for_route_settle(self) -> DashboardState:
        """Poll once per second after reload for the Vaadin route transition.

        PRODUCTS and AUTH_EXPIRED are definitive route outcomes and are
        returned immediately when detected. DASHBOARD is only accepted after
        the verified dashboard condition (URL + hash + DOM) holds for two
        consecutive polls, so the initial stale post-reload URL is never
        trusted. UNKNOWN is only accepted after two consecutive polls so a
        transient unknown right after a reload does not trigger an immediate
        recovery. Raises RecoveryError on timeout.
        """
        deadline = self._clock.now() + (self._settings.navigation_timeout_ms / 1000.0)
        verified_polls = 0
        unknown_polls = 0
        while self._clock.now() < deadline:
            self._clock.sleep(POLL_INTERVAL_SECONDS)
            state = classify_state(self._page)
            if state == DashboardState.DASHBOARD:
                unknown_polls = 0
                if _is_verified_dashboard(self._page):
                    verified_polls += 1
                    if verified_polls >= VERIFIED_DASHBOARD_POLLS:
                        log.info("Post-reload route settled on verified DASHBOARD")
                        return DashboardState.DASHBOARD
                    log.debug("Post-reload dashboard verified once; confirming")
                else:
                    verified_polls = 0
                    log.debug("Post-reload URL reports dashboard but UI not verified")
                continue
            verified_polls = 0
            if state in (DashboardState.PRODUCTS, DashboardState.AUTH_EXPIRED):
                log.info("Post-reload route settled on %s", state)
                return state
            # UNKNOWN: only accepted after consecutive polls so a transient
            # unknown right after reload does not trigger immediate recovery.
            unknown_polls += 1
            if unknown_polls >= 2:
                log.info("Post-reload route settled on UNKNOWN (consecutive polls)")
                return DashboardState.UNKNOWN
            log.debug("Post-reload UNKNOWN (%d consecutive); confirming", unknown_polls)
        raise RecoveryError("Route did not settle after reload")

    def _settle_auth_route(self) -> DashboardState:
        """Poll the route for up to AUTH_SETTLE_SECONDS when AUTH_EXPIRED is detected.

        If the page returns to PRODUCTS or a verified DASHBOARD (e.g. false
        session-closed redirect or temporary CAS navigation), returns that state immediately.
        Otherwise returns the final observed state (AUTH_EXPIRED or UNKNOWN) after deadline.
        Unverified DASHBOARD observations are never returned as settled DASHBOARD state.
        """
        deadline = self._clock.now() + AUTH_SETTLE_SECONDS
        last_state = DashboardState.AUTH_EXPIRED
        while self._clock.now() < deadline:
            self._clock.sleep(POLL_INTERVAL_SECONDS)
            state = classify_state(self._page)
            if state == DashboardState.PRODUCTS:
                return DashboardState.PRODUCTS
            if state == DashboardState.DASHBOARD:
                if _is_verified_dashboard(self._page):
                    return DashboardState.DASHBOARD
                last_state = DashboardState.UNKNOWN
            else:
                last_state = state
        return last_state

    def _handle_auth_expired(self) -> bool:
        """Handle AUTH_EXPIRED state with bounded route settling to prevent false relogins."""
        settled_state = self._settle_auth_route()
        if settled_state == DashboardState.PRODUCTS:
            log.info("Auth expired route settled to PRODUCTS; navigating to dashboard")
            return self._handle_products_state()
        if settled_state == DashboardState.DASHBOARD:
            log.info("Auth expired route settled to verified DASHBOARD")
            self._consecutive_recoveries = 0
            return False
        if settled_state == DashboardState.AUTH_EXPIRED:
            log.info("Session expired; initiating re-authentication")
            return self._relogin_and_navigate()
        log.warning("Auth expired route settled to %s; starting bounded recovery", settled_state)
        return self._recover_to_dashboard()

    def _perform_relogin(self) -> None:
        """Perform full re-authentication flow.

        authenticate_cmp records a fresh login attempt timestamp (via the
        injected clock and configured timezone), obtains a new OTP, and
        completes the full CAS login flow, finishing on the products page.
        We then click the real SPA Dashboard menu to reach the dashboard.
        """
        log.info("Requesting new OTP for re-authentication")
        authenticate_cmp(
            settings=self._settings,
            otp_provider=self._otp_provider,
            page=self._page,
            clock=self._clock,
        )

        # Navigate back to dashboard via the real SPA menu click (never a
        # direct goto to #!dashboard).
        navigate_to_dashboard(self._page, self._settings, self._clock)
        log.info("Re-authentication successful, navigated to dashboard")

    def _relogin_and_navigate(self) -> bool:
        """Re-authenticate and navigate to the verified dashboard."""
        try:
            self._perform_relogin()
        except (RecoveryError, AuthenticationRequiredError):
            log.error("Relogin navigation failed; starting bounded recovery")
            return self._recover_to_dashboard()
        # Only reset the counter after a verified dashboard state.
        self._consecutive_recoveries = 0
        return True

    def _check_recovery_limit(self) -> None:
        """Check if recovery limit has been exceeded."""
        if self._consecutive_recoveries >= self._settings.recovery_retry_limit:
            log.error("Recovery limit (%d) exceeded, terminating", self._settings.recovery_retry_limit)
            raise RecoveryExhaustedError(
                f"Maximum recovery attempts ({self._settings.recovery_retry_limit}) exceeded"
            )

    def shutdown(self) -> None:
        """Shutdown the monitor."""
        log.info("Monitor shutdown")
