"""CMP Dashboard monitoring module.

Monitors the Telkomsel CMP dashboard for GMF, periodically refreshing
and handling session expiry.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Protocol

from config import Settings
from cmp_auth import authenticate_cmp, AuthenticationError
from imap_client import OtpProviderProtocol

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


def classify_state(page: object) -> DashboardState:
    """Classify current page state based on URL.

    Returns DashboardState enum based on URL parsing:
    - DASHBOARD: HTTPS, approved host, fragment #!dashboard
    - PRODUCTS: HTTPS, approved host, fragment #!products
    - AUTH_EXPIRED: HTTPS, approved host, /cas/ path with visible login form or OTP form
    - UNKNOWN: anything else (including foreign-host lookalikes)
    """
    from urllib.parse import urlparse

    url = page.url if hasattr(page, 'url') else ""
    if not url:
        return DashboardState.UNKNOWN

    parsed = urlparse(url)

    # Must be HTTPS
    if parsed.scheme != "https":
        return DashboardState.UNKNOWN

    # Must be on approved host
    if parsed.hostname != "ep.iotcc.telkomsel.com":
        return DashboardState.UNKNOWN

    # Check fragment for dashboard/products
    if parsed.fragment == "!dashboard":
        return DashboardState.DASHBOARD
    elif parsed.fragment == "!products":
        return DashboardState.PRODUCTS

    # Check for CAS/login paths on approved host
    if parsed.path in ("/cas/login", "/cas/login/"):
        # Check if login form is visible (#username and #password) OR OTP form is visible (#token)
        try:
            has_login_form = page.locator("#username").is_visible() and page.locator("#password").is_visible()
            has_otp_form = page.locator("#token").is_visible()
            if has_login_form or has_otp_form:
                return DashboardState.AUTH_EXPIRED
        except Exception:
            pass
        # CAS path but no recognized form
        return DashboardState.UNKNOWN

    return DashboardState.UNKNOWN


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

        # Try to navigate back to dashboard
        try:
            self._page.goto(
                self._settings.cmp_dashboard_url,
                timeout=self._settings.navigation_timeout_ms,
                wait_until="domcontentloaded",
            )
        except Exception as nav_exc:
            log.error("Navigation to dashboard failed during recovery: %s", type(nav_exc).__name__)
            # Navigation failed, counter retained, will be checked on next cycle
            raise RecoveryError("Dashboard recovery failed") from nav_exc

        # Verify we're back at dashboard
        recovery_state = classify_state(self._page)
        if recovery_state != DashboardState.DASHBOARD:
            log.error("Recovery navigation did not reach dashboard, state: %s", recovery_state)
            # Not at dashboard, counter retained
            raise RecoveryError("Dashboard recovery failed: unexpected state")

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
                self._clock.sleep(self._settings.refresh_interval_seconds)
                try:
                    self._page.reload()
                except Exception:
                    log.error("Dashboard reload failed; starting bounded recovery")
                    return self._recover_to_dashboard()
                new_state = classify_state(self._page)

                if new_state == DashboardState.PRODUCTS:
                    log.info("Navigating back to dashboard")
                    self._page.goto(
                        self._settings.cmp_dashboard_url,
                        timeout=self._settings.navigation_timeout_ms,
                        wait_until="domcontentloaded",
                    )
                    if classify_state(self._page) != DashboardState.DASHBOARD:
                        self._recover_to_dashboard()
                    else:
                        self._consecutive_recoveries = 0
                    return False
                elif new_state == DashboardState.AUTH_EXPIRED:
                    log.info("Session expired, initiating re-authentication")
                    self._perform_relogin()
                    # Verify dashboard state before resetting counter
                    if classify_state(self._page) == DashboardState.DASHBOARD:
                        self._consecutive_recoveries = 0
                    return True
                elif new_state == DashboardState.DASHBOARD:
                    # Still at dashboard - normal case
                    self._consecutive_recoveries = 0
                else:
                    # Unknown state after reload - invoke bounded recovery
                    log.warning("Unknown state detected after reload, initiating recovery")
                    return self._recover_to_dashboard()

                return False

            elif current_state == DashboardState.PRODUCTS:
                log.info("Redirected to products, navigating to dashboard")
                try:
                    self._page.goto(
                        self._settings.cmp_dashboard_url,
                        timeout=self._settings.navigation_timeout_ms,
                        wait_until="domcontentloaded",
                    )
                except Exception:
                    log.error("Navigation to dashboard failed: %s", type(Exception).__name__)
                    log.error("Navigation to dashboard failed; starting bounded recovery")
                    return self._recover_to_dashboard()
                if classify_state(self._page) != DashboardState.DASHBOARD:
                    self._recover_to_dashboard()
                else:
                    self._consecutive_recoveries = 0
                return False

            elif current_state == DashboardState.AUTH_EXPIRED:
                log.info("Session expired, initiating re-authentication")
                self._perform_relogin()
                # Verify dashboard state before resetting counter
                if classify_state(self._page) == DashboardState.DASHBOARD:
                    self._consecutive_recoveries = 0
                return True

            else:
                # Unknown state - handle with bounded recovery
                log.warning("Unknown state detected, initiating recovery")
                return self._recover_to_dashboard()

        except RecoveryExhaustedError:
            # Already handled in _recover_to_dashboard or _check_recovery_limit
            # Do not increment counter again
            raise
        except RecoveryError:
            # Recovery navigation failed, counter already incremented in _recover_to_dashboard
            # Do not increment again
            raise
        except Exception as exc:
            # Do not log raw exception (may contain sensitive data)
            log.error("Monitoring error occurred: %s", type(exc).__name__)
            self._consecutive_recoveries += 1
            self._check_recovery_limit()
            # Re-raise to avoid silently masking errors
            raise

    def _perform_relogin(self) -> None:
        """Perform full re-authentication flow.

        authenticate_cmp records a fresh login attempt timestamp (via the
        injected clock and configured timezone), obtains a new OTP, and
        completes the full CAS login flow. We then navigate back to the
        dashboard.
        """
        log.info("Requesting new OTP for re-authentication")
        authenticate_cmp(
            settings=self._settings,
            otp_provider=self._otp_provider,
            page=self._page,
            clock=self._clock,
        )

        # Navigate back to dashboard
        self._page.goto(
            self._settings.cmp_dashboard_url,
            timeout=self._settings.navigation_timeout_ms,
            wait_until="domcontentloaded",
        )
        # Verify we're at dashboard
        if classify_state(self._page) != DashboardState.DASHBOARD:
            log.error("Relogin navigation did not reach dashboard")
            raise RecoveryError("Relogin failed: did not reach dashboard")
        log.info("Re-authentication successful, navigated to dashboard")

    def _check_recovery_limit(self) -> None:
        """Check if recovery limit has been exceeded."""
        if self._consecutive_recoveries >= self._settings.recovery_retry_limit:
            log.error("Recovery limit (%d) exceeded, terminating", self._settings.recovery_retry_limit)
            raise RecoveryExhaustedError(
                f"Maximum recovery attempts ({self._settings.recovery_retry_limit}) exceeded"
            )

        # Note: backoff sleep is now handled by _recover_to_dashboard to avoid double sleep

    def shutdown(self) -> None:
        """Shutdown the monitor."""
        log.info("Monitor shutdown")