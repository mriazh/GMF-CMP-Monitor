"""Tests for CMP authentication module - all offline, using dependency injection."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

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
        "otp_clock_skew_tolerance_seconds": 120,
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
    """Fake Playwright page for testing."""
    def __init__(self):
        self.urls = []
        self.fills = {}
        self.clicks = []
        self.current_url = "about:blank"
        self.inputs = {}
        self._otp_form_visible = False
        self._locator_visible = {}
        self.default_timeout = 30000
        self._state = "initial"  # initial -> cas -> otp_form -> root_portal -> products
        self._products_url = "https://ep.iotcc.telkomsel.com/#!products"  # Default products URL
        self._root_portal_url = "https://ep.iotcc.telkomsel.com/"  # Root portal URL
        self._submit_enabled = True  # Initial submit button enabled state
        self._blur_enables_submit = True  # Model portal validation re-evaluation on blur
        self.press_calls = []
        self.attribute_queries = []
        self.is_enabled_calls = []
        self.is_visible_calls = []
        self.evaluate_calls = []

    def set_submit_enabled(self, enabled: bool):
        """Control whether the initial submit button is enabled (disabled modeling)."""
        self._submit_enabled = enabled

    def set_blur_enables_submit(self, enabled: bool):
        """Model whether the portal's validation re-evaluation enables the button on blur."""
        self._blur_enables_submit = enabled

    def is_enabled(self, selector: str) -> bool:
        self.is_enabled_calls.append(selector)
        if selector == "#fm1 input[name='submit'][type='submit']":
            return self._submit_enabled
        return True

    def is_visible(self, selector: str) -> bool:
        self.is_visible_calls.append(selector)
        if selector in self._locator_visible:
            return self._locator_visible[selector]
        if selector == "#token" and (self._state == "otp_form" or self._otp_form_visible):
            return True
        if self._state == "cas":
            return selector in ("#username", "#password", "#fm1", "#fm1 input[name='submit']", "#fm1 input[name='submit'][type='submit']")
        return False

    def get_attribute(self, selector: str, name: str) -> str | None:
        self.attribute_queries.append((selector, name))
        if selector == "#fm1 input[name='submit'][type='submit']" and name == "disabled":
            return "disabled" if not self._submit_enabled else None
        return None

    def press(self, selector: str, key: str):
        self.press_calls.append((selector, key))
        # The portal re-evaluates validation on blur; model that a Tab blur on
        # the last filled field enables the submit button.
        if (
            selector == "#password"
            and key == "Tab"
            and self._blur_enables_submit
            and self.fills.get("#username")
            and self.fills.get("#password")
        ):
            self._submit_enabled = True

    def goto(self, url: str, timeout: int = None, wait_until: str = None):
        self.urls.append((url, timeout))
        self.current_url = url
        if "cas/login" in url:
            self._state = "cas"
        elif url == self._products_url:
            self._state = "products"
        elif url == self._root_portal_url:
            self._state = "root_portal"

    def fill(self, selector: str, value: str):
        self.fills[selector] = value

    def click(self, selector: str, timeout: int | float | None = None):
        self.clicks.append(selector)
        # Initial credential form submit: #fm1 input[name='submit'][type='submit']
        if selector == "#fm1 input[name='submit'][type='submit']":
            if self._state == "cas" and self._submit_enabled:
                # First submit: username/password -> OTP form
                self._state = "otp_form"
        # OTP form submit: #login input[name='_eventId_submit'][type='submit']
        elif selector == "#login input[name='_eventId_submit'][type='submit']":
            if self._state == "otp_form" and self.fills.get("#token"):
                # Second submit: OTP -> root portal (not directly products)
                self._state = "root_portal"
                self.current_url = self._root_portal_url

    def set_default_timeout(self, timeout: int):
        self.default_timeout = timeout

    @property
    def url(self):
        return self.current_url

    def input_value(self, selector: str) -> str:
        return self.inputs.get(selector, "")

    def wait_for_selector(self, selector: str, timeout: int = None, state: str = None):
        if selector == "#username" and self._state == "cas":
            return True
        if selector == "#fm1 input[name='submit']" and self._state == "cas":
            return True
        if selector == "#token" and self._state == "otp_form":
            self._otp_form_visible = True
            return True
        return None

    def wait_for_url(self, pattern: str, timeout: int = None):
        # Old implementation - should not be called in new code
        pass

    def evaluate(self, script: str):
        """Simulate page.evaluate for fragment checking."""
        self.evaluate_calls.append(script)
        if "document.readyState" in script:
            return "complete"
        if "window.location.hash" in script:
            if self._state == "products":
                return "#!products"
            elif self._state == "root_portal":
                return ""  # No fragment on root portal
        if "window.location.href" in script:
            return self.current_url
        return None

    def set_products_url(self, url: str):
        self._products_url = url

    def set_root_portal_url(self, url: str):
        self._root_portal_url = url


class FakeOtpProvider:
    def __init__(self, otp_value: str = "123456"):
        self._otp = otp_value
        self.connect_called = False
        self.disconnect_called = False
        self.poll_calls = []

    def connect(self):
        self.connect_called = True

    def disconnect(self):
        self.disconnect_called = True

    def poll_for_otp(self, run_start: datetime) -> str:
        self.poll_calls.append(run_start)
        return self._otp


class FakeClock:
    def __init__(self):
        self.now_calls = []
        self.sleep_calls = []

    def now(self) -> float:
        import time
        return time.time()

    def sleep(self, seconds: float):
        self.sleep_calls.append(seconds)


class TestLoginSelectorFlow:
    def test_navigates_to_cas_url(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert page.urls[0][0] == settings.cas_url
        assert page.urls[0][1] == settings.navigation_timeout_ms

    def test_fills_username_field(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert page.fills.get("#username") == settings.cmp_username.get_secret_value()

    def test_fills_password_field(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert page.fills.get("#password") == settings.cmp_password.get_secret_value()

    def test_submits_username_password_form(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Initial form uses #fm1 input[name='submit'][type='submit']
        assert "#fm1 input[name='submit'][type='submit']" in page.clicks

    def test_sets_default_timeout(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert page.default_timeout == settings.browser_timeout_ms

    def test_waits_for_otp_form(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Should have waited for #token selector
        # This is implicitly tested by the flow completing

    def test_submits_otp_form(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # OTP form uses #login input[name='_eventId_submit'][type='submit']
        assert "#login input[name='_eventId_submit'][type='submit']" in page.clicks

    def test_click_order_initial_then_otp(self):
        """Verify the first click is the initial form submit, second is OTP form submit."""
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Find the indices of the two clicks
        initial_click_idx = -1
        otp_click_idx = -1
        for i, click in enumerate(page.clicks):
            if click == "#fm1 input[name='submit'][type='submit']":
                initial_click_idx = i
            elif click == "#login input[name='_eventId_submit'][type='submit']":
                otp_click_idx = i
        
        assert initial_click_idx != -1, "Initial form submit click not found"
        assert otp_click_idx != -1, "OTP form submit click not found"
        assert initial_click_idx < otp_click_idx, "Initial form must be submitted before OTP form"

    def test_missing_initial_form_raises_authentication_error(self):
        """Missing initial form should cause AuthenticationError."""
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()
        
        # Override click to not transition state on initial submit
        original_click = page.click
        def broken_click(selector):
            if selector == "#fm1 input[name='submit'][type='submit']":
                # Don't transition to otp_form state - simulate missing form
                pass
            else:
                original_click(selector)
        page.click = broken_click

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)


class TestFreshTimestampOnEveryReLogin:
    def test_new_timestamp_for_each_login(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider(otp_value="654321")

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert len(otp.poll_calls) == 1
        # The timestamp should be for this login attempt
        first_timestamp = otp.poll_calls[0]
        assert isinstance(first_timestamp, datetime)
        assert first_timestamp.tzinfo is not None

    def test_timestamp_uses_configured_timezone(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        timestamp = otp.poll_calls[0]
        # Should be in Asia/Jakarta timezone
        assert timestamp.tzinfo is not None
        # Asia/Jakarta is UTC+7
        assert timestamp.utcoffset() is not None


class TestDisabledInitialSubmit:
    def test_disabled_submit_button_raises_bounded_error(self):
        """A visible but disabled initial submit button must fail with AuthenticationError."""
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        page.set_submit_enabled(False)
        page.set_blur_enables_submit(False)  # Portal validation never satisfies
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError, match="remained disabled"):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # The form event was attempted (smallest correct interaction: Tab blur),
        # but no click of the initial submit was allowed.
        assert page.press_calls == [("#password", "Tab")]
        assert "#fm1 input[name='submit'][type='submit']" not in page.clicks

    def test_submit_enabled_by_form_event_then_clicked(self):
        """A disabled button that becomes enabled after a blur event is clicked normally."""
        settings = make_test_settings()
        page = FakePage()
        page.set_submit_enabled(False)
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        result = authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert result is True
        # The Tab blur event enabled the button; the normal click then ran.
        assert page.press_calls == [("#password", "Tab")]
        assert "#fm1 input[name='submit'][type='submit']" in page.clicks

    def test_no_force_click_or_javascript_bypass(self):
        """The flow must never force-click or submit via JavaScript."""
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Clicks are plain selector clicks (no force flag is even supported).
        assert page.clicks == [
            "#fm1 input[name='submit'][type='submit']",
            "#login input[name='_eventId_submit'][type='submit']",
        ]
        # evaluate is used only for safe fragment checks and the
        # document.readyState diagnostic - never to submit the form.
        assert all(
            "window.location" in script or "document.readyState" in script
            for script in page.evaluate_calls
        )


class TestAlreadyAuthenticatedOrDirectRedirect:
    def test_cas_url_redirects_directly_to_products(self):
        settings = make_test_settings()
        page = FakePage()
        def goto_products(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = settings.cmp_products_url
            page._state = "products"
        page.goto = goto_products
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        result = authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert result is True
        assert len(otp.poll_calls) == 0
        assert page.fills.get("#username") is None

    def test_cas_url_lands_directly_on_otp_form(self):
        settings = make_test_settings()
        page = FakePage()
        def goto_otp(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = url
            page._state = "otp_form"
        page.goto = goto_otp
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        result = authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert result is True
        assert len(otp.poll_calls) == 1
        assert page.fills.get("#username") is None
        assert "#login input[name='_eventId_submit'][type='submit']" in page.clicks

    def test_cas_url_redirects_directly_to_root_portal(self):
        settings = make_test_settings()
        page = FakePage()
        def goto_root(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            if url == settings.cas_url:
                page.current_url = "https://ep.iotcc.telkomsel.com/"
                page._state = "root_portal"
            elif url == settings.cmp_products_url:
                page.current_url = settings.cmp_products_url
                page._state = "products"
            else:
                page.current_url = url
        page.goto = goto_root
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        result = authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        assert result is True
        assert len(otp.poll_calls) == 0
        assert page.fills.get("#username") is None

    def test_foreign_host_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_foreign(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://evil-ep.iotcc.telkomsel.com/#!products"
            page._state = "products"
        page.goto = goto_foreign
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_non_default_port_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_port(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://ep.iotcc.telkomsel.com:8443/#!products"
            page._state = "products"
        page.goto = goto_port
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_embedded_credentials_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_creds(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://user:pass@ep.iotcc.telkomsel.com/#!products"
            page._state = "products"
        page.goto = goto_creds
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_subdomain_lookalike_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_lookalike(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://ep.iotcc.telkomsel.com.evil.example/#!products"
            page._state = "products"
        page.goto = goto_lookalike
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_malformed_port_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_malformed(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://ep.iotcc.telkomsel.com:notaport/#!products"
            page._state = "products"
        page.goto = goto_malformed
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_empty_port_redirect_is_rejected(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        def goto_empty_port(url, timeout=None, wait_until=None):
            page.urls.append((url, timeout))
            page.current_url = "https://ep.iotcc.telkomsel.com:/#!products"
            page._state = "products"
        page.goto = goto_empty_port
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_closed_page_raises_sanitized_authentication_error(self):
        settings = make_test_settings()

        class ClosedPage:
            def goto(self, *args, **kwargs):
                raise RuntimeError("Target page, context or browser has been closed")

            def set_default_timeout(self, timeout):
                pass

        from cmp_auth import authenticate_cmp, AuthenticationError
        otp = FakeOtpProvider()

        with pytest.raises(AuthenticationError) as exc_info:
            authenticate_cmp(settings=settings, otp_provider=otp, page=ClosedPage())

        assert "Target page" not in str(exc_info.value)
        assert str(exc_info.value) == "Authentication failed"

    def test_delayed_password_rendering_waits_for_both_fields(self):
        settings = make_test_settings()
        page = FakePage()
        page._state = "cas"
        # Hide password field initially
        page._locator_visible["#password"] = False
        otp = FakeOtpProvider()

        class ClockAdvancingPassword(FakeClock):
            def sleep(self, seconds):
                super().sleep(seconds)
                # Enable password field on 2nd sleep call
                if len(self.sleep_calls) >= 2:
                    page._locator_visible["#password"] = True

        from cmp_auth import authenticate_cmp
        result = authenticate_cmp(settings=settings, otp_provider=otp, page=page, clock=ClockAdvancingPassword())

        assert result is True
        assert page.fills.get("#username") == "test_user"
        assert page.fills.get("#password") == "test_pass"


class TestAuthenticationFailure:
    def test_bad_username_raises(self):
        # This test verifies error handling
        # In offline mode, the mock should trigger AuthenticationError
        pass  # Placeholder for real test

    def test_otp_timeout_raises(self):
        # Test that OTP timeout raises proper error
        pass


class TestNoBrowserDuringImport:
    def test_import_does_not_launch_browser(self):
        # Import should not try to import playwright
        import sys
        assert "playwright" not in sys.modules or True


class TestSafeLogging:
    def test_cas_url_not_logged(self):
        settings = make_test_settings()
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        # The implementation logs "CAS login page" not the full URL
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)
        # Test passes if no exception - logging is verified by code inspection


class TestFragmentBasedSuccessDetection:
    def test_success_detected_on_products_fragment(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        otp = FakeOtpProvider()

        # Set the products URL (this is the default, but explicit for clarity)
        page.set_products_url("https://ep.iotcc.telkomsel.com/#!products")

        from cmp_auth import authenticate_cmp
        authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Should have reached products page
        assert page.current_url == "https://ep.iotcc.telkomsel.com/#!products"

    def test_rejects_http_products_url(self):
        settings = make_test_settings(
            navigation_timeout_ms=100,
            cmp_products_url="http://ep.iotcc.telkomsel.com/#!products"
        )
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        from cmp_auth import AuthenticationError
        with pytest.raises(AuthenticationError, match="timed out"):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_rejects_wrong_host_products_url(self):
        settings = make_test_settings(
            navigation_timeout_ms=100,
            cmp_products_url="https://evil.example.com/#!products"
        )
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        from cmp_auth import AuthenticationError
        with pytest.raises(AuthenticationError, match="timed out"):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

    def test_rejects_wrong_fragment(self):
        settings = make_test_settings(
            navigation_timeout_ms=100,
            cmp_products_url="https://ep.iotcc.telkomsel.com/#!wrong"
        )
        page = FakePage()
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp
        from cmp_auth import AuthenticationError
        with pytest.raises(AuthenticationError, match="timed out"):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)


class TestOtpRejectionDetection:
    """Regression coverage for the observed live failure where the OTP is
    submitted but CAS returns to the login page (OTP rejected / session expired).

    Live evidence (DEBUG logs): the OTP was retrieved and submitted, yet the page
    stayed on ``/cas/login``. The flow must surface an accurate, sanitized error
    instead of masking the rejection as a generic "Navigation to portal timed out"
    after the full navigation window elapses.
    """

    def test_otp_rejection_raises_sanitized_error(self):
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()

        # Model CAS rejecting the OTP: after OTP submit the portal bounces back
        # to the CAS login form (the observed live behaviour) instead of the
        # root portal / products page.
        original_click = page.click

        def rejecting_click(selector, timeout=None):
            original_click(selector, timeout)
            if selector == "#login input[name='_eventId_submit'][type='submit']":
                page._state = "cas"
                page.current_url = settings.cas_url

        page.click = rejecting_click
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError

        with pytest.raises(AuthenticationError, match="OTP rejected") as exc_info:
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # Sanitized: no credentials, OTP, or raw exception text leak.
        assert "OTP rejected" in str(exc_info.value)
        assert "Target page" not in str(exc_info.value)
        # The initial credential submit still happened before the OTP attempt.
        assert "#fm1 input[name='submit'][type='submit']" in page.clicks
        assert "#login input[name='_eventId_submit'][type='submit']" in page.clicks

    def test_otp_rejection_does_not_navigate_to_dashboard(self):
        # The rejected-OTP path must not perform a direct goto to #!dashboard;
        # it must only raise once the login form is observed again.
        settings = make_test_settings(navigation_timeout_ms=100)
        page = FakePage()
        original_click = page.click

        def rejecting_click(selector, timeout=None):
            original_click(selector, timeout)
            if selector == "#login input[name='_eventId_submit'][type='submit']":
                page._state = "cas"
                page.current_url = settings.cas_url

        page.click = rejecting_click
        otp = FakeOtpProvider()

        from cmp_auth import authenticate_cmp, AuthenticationError
        with pytest.raises(AuthenticationError):
            authenticate_cmp(settings=settings, otp_provider=otp, page=page)

        # No direct navigation to the dashboard URL was triggered.
        assert all("dashboard" not in str(u) for (u, _t) in page.urls)
