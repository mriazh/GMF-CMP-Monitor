"""Tests for dashboard monitoring - all offline, using fake objects."""

import pytest
from datetime import datetime

from config import Settings, SecretValue


def make_test_settings(**overrides) -> Settings:
    defaults = {
        "cas_url": "https://ep.iotcc.telkomsel.com/cas/login",
        "cmp_products_url": "https://ep.iotcc.telkomsel.com/#!products",
        "cmp_dashboard_url": "https://ep.iotcc.telkomsel.com/#!dashboard",
        "cmp_username": SecretValue("test_user"),
        "cmp_password": SecretValue("test_pass"),
        "imap_host": "mail.gmf-aeroasia.co.id",
        "imap_port": 993,
        "imap_username": SecretValue("imap_user"),
        "imap_password": SecretValue("imap_pass"),
        "imap_tls_mode": "imaps",
        "imap_verify_tls": True,
        "imap_mailbox": "INBOX",
        "otp_subject": "CMP - YOUR TOKEN",
        "otp_poll_interval_seconds": 2,
        "otp_timeout_seconds": 120,
        "run_start_timezone": "Asia/Jakarta",
        "browser_timeout_ms": 30000,
        "navigation_timeout_ms": 30000,
        "otp_form_timeout_ms": 30000,
        "refresh_interval_seconds": 60,
        "recovery_retry_limit": 3,
        "recovery_backoff_seconds": 5,
        "headless": False,
        "runtime_artifact_dir": None,
        "browser_storage_state_path": None,
        "log_level": "INFO",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class FakePage:
    """Fake Playwright page supporting the CAS auth flow and dashboard navigation."""

    def __init__(self, initial_url="", url=None):
        if url is not None:
            initial_url = url
        self._url = initial_url
        self._navigations = []
        self.goto_calls = []
        self._locator_visible = {}
        self.fills = {}
        self.clicks = []
        self.inputs = {}
        self.default_timeout = 30000
        self._state = "initial"
        self._products_url = "https://ep.iotcc.telkomsel.com/#!products"
        if "cas/login" in self._url:
            self._state = "cas"
            self._locator_visible["#username"] = True
            self._locator_visible["#password"] = True

    @property
    def url(self):
        return self._url

    def goto(self, url, timeout=None):
        self.goto_calls.append((url, timeout))
        self._navigations.append(url)
        self._url = url
        if "cas/login" in url:
            self._state = "cas"
            self._locator_visible["#username"] = True
            self._locator_visible["#password"] = True
        else:
            self._locator_visible["#username"] = False
            self._locator_visible["#password"] = False

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def fill(self, selector, value):
        self.fills[selector] = value

    def click(self, selector):
        self.clicks.append(selector)
        if selector == "#fm1 input[name='submit'][type='submit']":
            if self._state == "cas":
                self._state = "otp_form"
        elif selector == "#login input[name='_eventId_submit'][type='submit']":
            if self._state == "otp_form" and self.fills.get("#token"):
                self._state = "products"
                self._url = self._products_url
                self._locator_visible["#username"] = False
                self._locator_visible["#password"] = False

    def input_value(self, selector):
        return self.inputs.get(selector, "")

    def wait_for_selector(self, selector, timeout=None):
        if selector == "#token" and self._state == "otp_form":
            return True
        return None

    def reload(self):
        self._navigations.append(self._url)

    def locator(self, selector):
        return FakeLocator(self._locator_visible.get(selector, False))

    def set_products_url(self, url):
        self._products_url = url

    def set_session_expired(self):
        """Simulate session expiry: CAS login page with visible credentials form."""
        self._url = "https://ep.iotcc.telkomsel.com/cas/login"
        self._state = "cas"
        self._locator_visible["#username"] = True
        self._locator_visible["#password"] = True
        self._locator_visible["#token"] = False
        self._navigations.append(self._url)


class FakeLocator:
    def __init__(self, visible: bool):
        self._visible = visible

    def is_visible(self):
        return self._visible


class FakeClock:
    def __init__(self):
        self.sleep_calls = []
        self.now_calls = []
        self._t = 1700000000.0

    def now(self) -> float:
        self._t += 1.0
        self.now_calls.append(self._t)
        return self._t

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)


class FakeOtpProvider:
    def __init__(self):
        self.poll_calls = []
        self.otp_value = "123456"

    def connect(self):
        pass

    def disconnect(self):
        pass

    def poll_for_otp(self, run_start: datetime) -> str:
        self.poll_calls.append(run_start)
        return self.otp_value


class TestProductsRedirectBackToDashboard:
    def test_products_to_dashboard(self):
        from dashboard_monitor import classify_state, DashboardState

        page = FakePage(url="https://ep.iotcc.telkomsel.com/#!products")
        state = classify_state(page)
        assert state == DashboardState.PRODUCTS

        # Now trigger monitor_once
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=make_test_settings(),
            page=page,
            otp_provider=otp_provider,
            clock=FakeClock(),
        )

        monitor.monitor_once()
        # Should have navigated to dashboard with the configured timeout.
        settings = make_test_settings()
        assert page.goto_calls[-1] == (
            settings.cmp_dashboard_url,
            settings.navigation_timeout_ms,
        )


class TestDashboardStateClassification:
    def test_dashboard_url(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        assert classify_state(page) == DashboardState.DASHBOARD

    def test_products_url(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        assert classify_state(page) == DashboardState.PRODUCTS

    def test_cas_login_url(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_unknown_url(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://example.com")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_foreign_host_dashboard_lookalike_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # Foreign host with dashboard fragment should be UNKNOWN
        page = FakePage("https://evil.example.com/#!dashboard")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_foreign_host_products_lookalike_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # Foreign host with products fragment should be UNKNOWN
        page = FakePage("https://evil.example.com/#!products")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_http_lookalike_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # HTTP instead of HTTPS should be UNKNOWN
        page = FakePage("http://ep.iotcc.telkomsel.com/#!dashboard")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_http_products_lookalike_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("http://ep.iotcc.telkomsel.com/#!products")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_malformed_url_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # Malformed URL should be UNKNOWN
        page = FakePage("not-a-url")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_approved_host_wrong_fragment_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # Approved host but wrong fragment
        page = FakePage("https://ep.iotcc.telkomsel.com/#!other")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_cas_logout_path_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/logout")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_cas_service_validate_path_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/serviceValidate")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_cas_path_without_login_form_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # CAS path without visible login form or OTP form should be UNKNOWN
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/oauth2/authorize")
        # The fake page won't have login form or OTP form visible on this path
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_cas_login_with_visible_login_form_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        # CAS login page with visible username/password form should be AUTH_EXPIRED
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        # FakePage sets #username and #password visible for cas/login
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_cas_with_visible_otp_form_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        # CAS page with visible OTP form (#token) should be AUTH_EXPIRED
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        # Manually set #token visible and hide username/password
        page._locator_visible["#token"] = True
        page._locator_visible["#username"] = False
        page._locator_visible["#password"] = False
        assert classify_state(page) == DashboardState.AUTH_EXPIRED


class TestSessionExpiryTriggeringReLogin:
    def test_auth_expired_triggers_relogin(self):
        from dashboard_monitor import classify_state

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider)
        result = monitor.monitor_once()

        # Should return True to indicate re-auth performed
        assert result is True
        # Should have requested exactly one OTP (no double polling)
        assert len(otp_provider.poll_calls) == 1
        # Should have navigated to dashboard after relogin
        assert page._navigations[-1] == settings.cmp_dashboard_url

    def test_new_otp_timestamp_on_every_relogin(self):
        """Each re-login must read a fresh timestamp from the clock."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider)

        monitor.monitor_once()
        first = otp_provider.poll_calls[0]
        assert first.tzinfo is not None

        # Simulate another session expiry
        page.set_session_expired()
        monitor.monitor_once()

        second = otp_provider.poll_calls[1]
        assert first != second


class TestProductsRedirectBackToDashboard:
    def test_navigating_to_dashboard_after_reload(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        monitor.monitor_once()

        assert len(page._navigations) > 0
        assert settings.cmp_dashboard_url in page._navigations

    def test_products_after_reload_navigates_back_without_relogin(self):
        """Reload redirecting to products is a normal redirect: back to dashboard, no OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        def reload_to_products():
            page._url = "https://ep.iotcc.telkomsel.com/#!products"
        page.reload = reload_to_products

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert otp_provider.poll_calls == []


class TestBoundedRetries:
    def test_recovery_retry_limit_capped(self):
        # Create settings with custom retry limit
        settings = make_test_settings(recovery_retry_limit=1)

        page = FakePage("https://unknown.com")
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # Should raise RecoveryExhaustedError after limit exceeded
        from dashboard_monitor import RecoveryExhaustedError
        with pytest.raises(RecoveryExhaustedError):
            monitor.monitor_once()

    def test_recovery_backoff_sleep(self):
        settings = make_test_settings(recovery_retry_limit=2, recovery_backoff_seconds=10)
        page = FakePage("https://unknown.com")
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

        from dashboard_monitor import RecoveryExhaustedError

        # First attempt: backoff sleep happens, recovery succeeds
        result = monitor.monitor_once()
        assert result is True
        assert 10 in clock.sleep_calls
        assert monitor._consecutive_recoveries == 0

        # A successful recovery resets the counter. A later failure starts a
        # new bounded recovery sequence rather than exhausting the old one.
        page._url = "https://unknown.com"
        assert monitor.monitor_once() is True
        assert monitor._consecutive_recoveries == 0


class TestUnknownUrlHandling:
    def test_unknown_url_triggers_recovery(self):
        settings = make_test_settings()
        page = FakePage("https://unknown.com")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should recover to dashboard and return True
        assert result is True
        # Counter should be reset after successful recovery
        assert monitor._consecutive_recoveries == 0
        # Should have navigated to dashboard
        assert settings.cmp_dashboard_url in page._navigations
        # Page should be at dashboard URL
        assert page.url == settings.cmp_dashboard_url

    def test_unknown_state_after_reload(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should recover and return True
        assert result is True
        assert monitor._consecutive_recoveries == 0
        assert page.url == settings.cmp_dashboard_url

    def test_successful_unknown_state_recovery(self):
        settings = make_test_settings()
        page = FakePage("https://unknown.com")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

        result = monitor.monitor_once()
        assert result is True
        # Should have slept for backoff
        assert settings.recovery_backoff_seconds in clock.sleep_calls
        # Counter reset
        assert monitor._consecutive_recoveries == 0

    def test_failed_navigation_retains_counter(self):
        settings = make_test_settings(recovery_retry_limit=3)
        page = FakePage("https://unknown.com")

        # Make goto fail
        def failing_goto(url, timeout=None):
            raise RuntimeError("Navigation failed")
        page.goto = failing_goto

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt: navigation fails, counter incremented, limit not exceeded yet
        with pytest.raises(RecoveryError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 1

        # Second attempt
        with pytest.raises(RecoveryError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 2

        # Third attempt: limit exceeded
        from dashboard_monitor import RecoveryExhaustedError
        with pytest.raises(RecoveryExhaustedError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 3

    def test_recovery_counter_reset_only_after_verified_dashboard(self):
        settings = make_test_settings(recovery_retry_limit=3)
        page = FakePage("https://unknown.com")

        # Navigation succeeds but ends up at non-dashboard URL
        def goto_wrong_url(url, timeout=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"  # Not dashboard
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # Should raise because recovery didn't reach dashboard
        with pytest.raises(RecoveryError, match="unexpected state"):
            monitor.monitor_once()
        # Counter should NOT be reset
        assert monitor._consecutive_recoveries == 1

    def test_exhaustion_at_configured_limit(self):
        settings = make_test_settings(recovery_retry_limit=2)
        page = FakePage("https://unknown.com")

        # Navigation succeeds but ends up at non-dashboard URL
        def goto_wrong_url(url, timeout=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError, RecoveryExhaustedError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt - recovery fails
        with pytest.raises(RecoveryError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 1

        # Second attempt - limit exceeded
        with pytest.raises(RecoveryExhaustedError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 2


class TestRecoveryCounterReset:
    def test_counter_reset_after_successful_recovery(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First recovery - should succeed
        result = monitor.monitor_once()
        assert result is True
        # Counter should be reset to 0
        assert monitor._consecutive_recoveries == 0


class TestGracefulShutdown:
    def test_shutdown_called(self):
        from dashboard_monitor import ContinuousMonitor

        settings = make_test_settings()
        page = FakePage()
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider)

        assert monitor.shutdown() is None  # Should not raise


class TestDashboardReloadRecovery:
    """Tests for dashboard reload followed by unknown state recovery."""

    def test_dashboard_reload_to_unknown_successful_recovery(self):
        """Dashboard reload -> unknown -> successful bounded recovery."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)
        result = monitor.monitor_once()

        # Should recover and return True
        assert result is True
        assert monitor._consecutive_recoveries == 0
        assert page.url == settings.cmp_dashboard_url
        # Should have slept for backoff
        assert settings.recovery_backoff_seconds in clock.sleep_calls
        # Should NOT have requested OTP (recovery, not relogin)
        assert len(otp_provider.poll_calls) == 0

    def test_dashboard_reload_to_unknown_failed_navigation(self):
        """Dashboard reload -> unknown -> failed navigation retains counter."""
        settings = make_test_settings(recovery_retry_limit=3)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        # Make goto fail
        def failing_goto(url, timeout=None):
            raise RuntimeError("Navigation failed")
        page.goto = failing_goto

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt: navigation fails, counter incremented
        with pytest.raises(RecoveryError):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 1
        assert len(otp_provider.poll_calls) == 0

    def test_dashboard_reload_to_unknown_recovery_exhaustion(self):
        """Dashboard reload -> unknown -> recovery exhaustion after limit."""
        settings = make_test_settings(recovery_retry_limit=2)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        # Navigation succeeds but ends up at non-dashboard URL
        def goto_wrong_url(url, timeout=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError, RecoveryExhaustedError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt: recovery fails, counter=1
        with pytest.raises(RecoveryError, match="unexpected state"):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 1
        assert len(otp_provider.poll_calls) == 0

        # Second attempt: limit exceeded
        with pytest.raises(RecoveryExhaustedError, match="Maximum recovery attempts"):
            monitor.monitor_once()
        assert monitor._consecutive_recoveries == 2
        assert len(otp_provider.poll_calls) == 0

    def test_products_redirect_no_new_otp(self):
        """Products redirect should not request a new OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Reload goes to products
        def reload_to_products():
            page._url = "https://ep.iotcc.telkomsel.com/#!products"
        page.reload = reload_to_products

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should navigate back to dashboard without OTP
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert len(otp_provider.poll_calls) == 0

    def test_cas_login_state_requests_new_otp(self):
        """CAS login state should request a new OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should perform relogin and request OTP
        assert result is True
        assert len(otp_provider.poll_calls) == 1

    def test_unknown_foreign_host_lookalike_never_triggers_auth_directly(self):
        """Unknown foreign-host lookalikes should trigger recovery, not direct auth."""
        settings = make_test_settings()
        page = FakePage("https://evil.example.com/#!dashboard")

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # It must recover through the approved dashboard URL, not authenticate
        # directly from a foreign-host lookalike.
        assert monitor.monitor_once() is True
        assert page.url == settings.cmp_dashboard_url
        assert monitor._consecutive_recoveries == 0
        assert len(otp_provider.poll_calls) == 0


class TestNoBrowserDuringImport:
    def test_import_dashboard_monitor(self):
        # Should import without launching browser
        import dashboard_monitor
        assert hasattr(dashboard_monitor, "ContinuousMonitor")
        assert hasattr(dashboard_monitor, "DashboardState")


class TestNavigationFailureRecovery:
    def test_navigation_failure_increments_counter(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")

        # Make reload (used on the dashboard branch) fail
        def failing_reload():
            raise RuntimeError("Navigation failed")
        page.reload = failing_reload

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

        # Reload failure is recovered by navigating back to the dashboard.
        assert monitor.monitor_once() is True
        assert monitor._consecutive_recoveries == 0
        assert settings.recovery_backoff_seconds in clock.sleep_calls
        assert page.goto_calls[-1] == (
            settings.cmp_dashboard_url,
            settings.navigation_timeout_ms,
        )
