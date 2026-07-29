"""Tests for dashboard monitoring - all offline, using fake objects.

The fakes model the real Vaadin SPA behavior: a direct ``page.goto`` to
``#!dashboard`` changes the URL but does NOT render the dashboard view, so
``_is_verified_dashboard`` stays False until the real Dashboard menu item is
clicked. Delayed route transitions (post-reload resets to products, menu
click bounces) are modelled with scheduled transitions.
"""

import pytest
from datetime import datetime
from urllib.parse import urlparse

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
    """Fake Playwright page modelling the Vaadin SPA dashboard navigation.

    Key modelling rule: ``goto`` to ``#!dashboard`` changes the URL but never
    makes the dashboard "ready" (no selected menu item, no dashboard view), so
    the false-positive detection the real portal exhibits is reproduced here.
    The dashboard only becomes ready when the real menu item is clicked.
    """

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
        self._dashboard_url = "https://ep.iotcc.telkomsel.com/#!dashboard"
        self._menu_visible = False
        self._menu_after = None
        self._menu_clicks = 0
        self._dashboard_ready = "#!dashboard" in self._url
        self._pending = None
        self._post_reload = None
        self._menu_bounce = None
        self._post_click_transition = None
        self._hash_override = None
        self._click_delay_reads = None
        self._click_transition_delay = None
        self._ready_after = None
        if "cas/login" in self._url:
            self._state = "cas"
            self._locator_visible["#username"] = True
            self._locator_visible["#password"] = True
        elif "#!products" in self._url:
            self._state = "products"
        elif "#!dashboard" in self._url:
            self._state = "dashboard"

    # -- state machine -------------------------------------------------

    @property
    def url(self):
        if self._pending is not None:
            self._pending["remaining"] -= 1
            if self._pending["remaining"] <= 0:
                self._apply_transition(self._pending)
                self._pending = None
                # Chain post-click transition if configured (for hard bounce)
                if self._post_click_transition is not None:
                    # Convert after_reads to remaining for the next transition
                    self._pending = {
                        "remaining": self._post_click_transition["after_reads"],
                        "url": self._post_click_transition["url"],
                        "state": self._post_click_transition["state"],
                    }
                    self._post_click_transition = None
        if self._ready_after is not None:
            self._ready_after -= 1
            if self._ready_after <= 0:
                self._dashboard_ready = True
                self._ready_after = None
        return self._url

    def _apply_transition(self, config):
        """Apply a scheduled Vaadin route transition."""
        self._url = config["url"]
        self._state = config.get("state", "initial")
        # A route change never renders the dashboard view on its own unless the
        # transition explicitly carries a ready dashboard (used by the delayed
        # menu-click transition mode).
        self._dashboard_ready = bool(config.get("ready", False))
        if "cas/login" in self._url:
            self._state = "cas"
            self._locator_visible["#username"] = True
            self._locator_visible["#password"] = True
            self._locator_visible["#token"] = False
        else:
            self._locator_visible["#username"] = False
            self._locator_visible["#password"] = False
            self._locator_visible["#token"] = False
            if "#!products" in self._url:
                self._state = "products"
            elif "#!dashboard" in self._url:
                self._state = "dashboard"

    # -- Playwright-like API -------------------------------------------

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_calls.append((url, timeout, wait_until))
        self._navigations.append(url)
        self._url = url
        # Direct navigation never renders the Vaadin dashboard view.
        self._dashboard_ready = False
        if "cas/login" in url:
            self._state = "cas"
            self._locator_visible["#username"] = True
            self._locator_visible["#password"] = True
            self._locator_visible["#token"] = False
        else:
            self._locator_visible["#username"] = False
            self._locator_visible["#password"] = False
            self._locator_visible["#token"] = False
            if "#!products" in url:
                self._state = "products"
            elif "#!dashboard" in url:
                self._state = "dashboard"
            else:
                self._state = "initial"

    def reload(self):
        self._navigations.append(self._url)
        # A reload resets the Vaadin SPA: the URL may still report dashboard
        # but the UI is not ready until the router settles.
        self._dashboard_ready = False
        self._locator_visible["#token"] = False
        if self._post_reload is not None:
            self._pending = {
                "remaining": self._post_reload["after_reads"],
                "url": self._post_reload["url"],
                "state": self._post_reload["state"],
            }

    def evaluate(self, expression):
        if "document.readyState" in expression:
            return "complete"
        if "location.hash" in expression:
            if self._hash_override is not None:
                return self._hash_override
            return "#" + urlparse(self._url).fragment
        if "location.href" in expression:
            return self._url
        return None

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def fill(self, selector, value):
        self.fills[selector] = value

    def click(self, selector, timeout=None, **kwargs):
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

    def press(self, selector, key):
        # Model the CAS portal re-evaluating validation on blur: a Tab blur on
        # the last filled field enables the initial submit button.
        if selector == "#password" and key == "Tab":
            if self.fills.get("#username") and self.fills.get("#password"):
                self._submit_enabled = True

    def is_enabled(self, selector):
        if selector == "#fm1 input[name='submit'][type='submit']":
            return getattr(self, "_submit_enabled", True)
        return True

    def is_visible(self, selector):
        if self._state == "cas":
            return selector in (
                "#username",
                "#password",
                "#fm1",
                "#fm1 input[name='submit']",
                "#fm1 input[name='submit'][type='submit']",
            )
        return self._locator_visible.get(selector, False)

    def get_attribute(self, selector, name):
        if (
            selector == "#fm1 input[name='submit'][type='submit']"
            and name == "disabled"
            and not getattr(self, "_submit_enabled", True)
        ):
            return "disabled"
        return None

    def wait_for_selector(self, selector, timeout=None, state=None):
        if selector == "#username" and self._state == "cas":
            return True
        if selector == "#fm1 input[name='submit']" and self._state == "cas":
            return True
        if selector == "#token" and self._state == "otp_form":
            return True
        return None

    def locator(self, selector, has_text=None):
        if selector == "span.main-menu-item-caption":
            if self._menu_after is not None:
                self._menu_after -= 1
                if self._menu_after <= 0:
                    self._menu_visible = True
                    self._menu_after = None
            return FakeMenuLocator(self, has_text=has_text, visible=self._menu_visible)
        if selector in ("span.main-menu-item-caption.selected", "div.main-menu-item.selected"):
            return FakeMenuLocator(self, has_text=has_text, visible=self._dashboard_ready)
        if selector == "div.dashboard-view":
            return FakeLocator(self._dashboard_ready)
        return FakeLocator(self._locator_visible.get(selector, False))

    def get_by_text(self, text, exact=False):
        return FakeMenuLocator(self, has_text=text, visible=self._menu_visible)

    # -- test helpers ---------------------------------------------------

    def set_menu_visible(self, visible=True):
        self._menu_visible = visible

    def set_menu_visible_after_reads(self, n):
        """Make the Dashboard menu appear only after n visibility polls."""
        self._menu_after = n

    def set_dashboard_ready(self, ready=True):
        self._dashboard_ready = ready

    def set_hash(self, value):
        self._hash_override = value

    def set_products_url(self, url):
        self._products_url = url

    def set_session_expired(self):
        """Simulate session expiry: CAS login page with visible credentials form."""
        self._url = "https://ep.iotcc.telkomsel.com/cas/login"
        self._state = "cas"
        self._locator_visible["#username"] = True
        self._locator_visible["#password"] = True
        self._locator_visible["#token"] = False
        self._dashboard_ready = False
        self._navigations.append(self._url)

    def schedule_transition(self, target_url, target_state, after_reads=1):
        """Schedule a Vaadin route transition to fire after N url reads."""
        self._pending = {"remaining": after_reads, "url": target_url, "state": target_state}

    def set_post_reload(self, target_url, target_state, after_reads=1):
        """Make the next reload() settle on target_url after N url reads."""
        self._post_reload = {"after_reads": after_reads, "url": target_url, "state": target_state}

    def set_menu_click_bounce(self, target_url, target_state, after_reads=2, via_dashboard=False):
        """Make the next menu click bounce back after N url reads (once).

        If ``via_dashboard=True``, models a hard bounce: the click first transitions
        to the dashboard URL (with dashboard not ready), then after ``after_reads``
        more url reads transitions to ``target_url``/``target_state``. This models
        a route round-trip (products -> dashboard -> products) that the real
        Vaadin router may perform when a navigation is rejected.
        """
        if via_dashboard:
            # Hard bounce: first go to dashboard, then to target
            self._menu_bounce = {
                "after_reads": after_reads,
                "url": self._dashboard_url,
                "state": "dashboard",
                "ready": False,
                "bounce_back": {"after_reads": after_reads, "url": target_url, "state": target_state},
            }
        else:
            # Soft bounce: direct transition to target (legacy behavior)
            self._menu_bounce = {"after_reads": after_reads, "url": target_url, "state": target_state}

    def set_click_delay_reads(self, n):
        """Make the next menu click render the dashboard view after N url reads.

        Models a slow Vaadin route transition: the URL updates immediately on
        click but the dashboard DOM only becomes ready after n more url reads.
        """
        self._click_delay_reads = n

    def set_click_transition_delay(self, n):
        """Delay BOTH the URL/hash transition AND the dashboard DOM on the next click.

        Models a slow real-world Vaadin transition: after clicking Dashboard
        neither the URL nor the DOM changes for n url reads; only then does the
        page transition to the dashboard URL with the dashboard DOM ready.
        """
        self._click_transition_delay = n

    @property
    def menu_clicks(self):
        return self._menu_clicks


class FakeMenuLocator:
    """Fake Playwright Locator for the SPA Dashboard menu item."""

    def __init__(self, page, has_text=None, visible=False):
        self._page = page
        self._has_text = has_text
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._has_text or "Dashboard"

    def click(self):
        if not self._visible:
            raise RuntimeError("Dashboard menu not visible")
        self._page._menu_clicks += 1
        self._page._navigations.append(self._page._dashboard_url)
        if self._page._click_transition_delay is not None:
            # Model a slow real-world Vaadin transition: neither the URL/hash
            # nor the dashboard DOM changes for a while after the click; only
            # after n more url reads does the page transition to the dashboard
            # URL with the dashboard DOM ready.
            delay = self._page._click_transition_delay
            self._page._click_transition_delay = None
            self._page._pending = {
                "remaining": delay,
                "url": self._page._dashboard_url,
                "state": "dashboard",
                "ready": True,
            }
        elif self._page._click_delay_reads is not None:
            # Model a slow Vaadin route transition: the URL updates
            # immediately but the dashboard view only becomes ready after a
            # few more url reads (simulating a delayed render).
            self._page._url = self._page._dashboard_url
            self._page._state = "dashboard"
            self._page._dashboard_ready = False
            self._page._ready_after = self._page._click_delay_reads
            self._page._click_delay_reads = None
        elif self._page._menu_bounce is not None:
            bounce = self._page._menu_bounce
            self._page._menu_bounce = None
            if "bounce_back" in bounce:
                # Hard bounce: first transition to dashboard, then bounce back
                self._page._pending = {
                    "remaining": bounce["after_reads"],
                    "url": bounce["url"],
                    "state": bounce["state"],
                    "ready": bounce.get("ready", False),
                }
                self._page._post_click_transition = bounce["bounce_back"]
            else:
                # Soft bounce: direct transition to target
                self._page._pending = {
                    "remaining": bounce["after_reads"],
                    "url": bounce["url"],
                    "state": bounce["state"],
                }
        else:
            # Immediate transition to dashboard with DOM ready
            self._page._url = self._page._dashboard_url
            self._page._state = "dashboard"
            self._page._dashboard_ready = True


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


class VaadinBootingFakePage(FakePage):
    """FakePage variant that models a slow Vaadin 7 SPA bootstrap.

    Adds a ``context`` attribute so ``_is_playwright_page`` treats it like a real
    Playwright page and the ``_wait_for_vaadin_loading`` pre-wait is exercised.
    Use ``set_menu_visible_after_reads(n)`` to simulate the menu becoming visible
    only after ``n`` visibility polls.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = object()


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

    def test_session_closed_path_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        # Session-closed endpoint on the approved host triggers auto-relogin
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed?locale=en")
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_cas_login_without_form_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        page._locator_visible["#username"] = False
        page._locator_visible["#password"] = False
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_cas_login_with_query_params_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login/?service=foo")
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_session_closed_slash_with_query_params_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed/?locale=en")
        assert classify_state(page) == DashboardState.AUTH_EXPIRED

    def test_session_closed_extra_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed-extra")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_foreign_host_cas_login_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://evil-ep.iotcc.telkomsel.com/cas/login")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_http_cas_login_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("http://ep.iotcc.telkomsel.com/cas/login")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_auth_path_with_misleading_fragment_is_auth_expired(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login#!dashboard")
        assert classify_state(page) == DashboardState.AUTH_EXPIRED
        page2 = FakePage("https://ep.iotcc.telkomsel.com/session-closed#!products")
        assert classify_state(page2) == DashboardState.AUTH_EXPIRED

    def test_foreign_host_session_closed_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        # Foreign-host session-closed lookalike must stay UNKNOWN (strict host check)
        page = FakePage("https://evil.example.com/session-closed?locale=en")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_non_default_port_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com:8443/#!dashboard")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_embedded_credentials_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://user:pass@ep.iotcc.telkomsel.com/#!dashboard")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_subdomain_lookalike_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com.evil.example/#!dashboard")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_page_url_exception_returns_unknown(self):
        from dashboard_monitor import classify_state, DashboardState

        class ClosedPage:
            @property
            def url(self):
                raise RuntimeError("Target page, context or browser has been closed")

        assert classify_state(ClosedPage()) == DashboardState.UNKNOWN

    def test_malformed_port_products_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com:notaport/#!products")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_malformed_port_cas_login_is_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com:notaport/cas/login")
        assert classify_state(page) == DashboardState.UNKNOWN

    def test_empty_port_urls_are_unknown(self):
        from dashboard_monitor import classify_state, DashboardState
        assert classify_state(FakePage("https://ep.iotcc.telkomsel.com:/#!products")) == DashboardState.UNKNOWN
        assert classify_state(FakePage("https://ep.iotcc.telkomsel.com:/#!dashboard")) == DashboardState.UNKNOWN
        assert classify_state(FakePage("https://ep.iotcc.telkomsel.com:/cas/login")) == DashboardState.UNKNOWN
        assert classify_state(FakePage("https://ep.iotcc.telkomsel.com:/session-closed")) == DashboardState.UNKNOWN


class TestVerifiedDashboard:
    """The real dashboard verifier requires URL + hash + DOM together."""

    def test_requires_url_hash_and_dom(self):
        from dashboard_monitor import _is_verified_dashboard, _dashboard_dom_ready
        # URL and hash correct but DOM not ready -> not verified
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(False)
        assert not _is_verified_dashboard(page)
        # DOM ready -> verified
        page.set_dashboard_ready(True)
        assert _is_verified_dashboard(page)
        assert _dashboard_dom_ready(page)

    def test_hash_mismatch_is_not_verified(self):
        from dashboard_monitor import _is_verified_dashboard
        # URL reports dashboard but window.location.hash disagrees
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(True)
        page.set_hash("#!products")
        assert not _is_verified_dashboard(page)

    def test_goto_dashboard_does_not_make_dashboard_ready(self):
        from dashboard_monitor import _is_verified_dashboard
        # A direct goto to #!dashboard changes the URL but never renders the
        # Vaadin dashboard view, so it must NOT count as verified.
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.goto(settings.cmp_dashboard_url, timeout=1000, wait_until="commit")
        assert page.url == settings.cmp_dashboard_url
        assert not _is_verified_dashboard(page)

    def test_foreign_host_never_verified_even_with_dom(self):
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://evil.example.com/#!dashboard")
        page.set_dashboard_ready(True)
        assert not _is_verified_dashboard(page)

    def test_non_default_port_never_verified(self):
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com:8443/#!dashboard")
        page.set_dashboard_ready(True)
        assert not _is_verified_dashboard(page)

    def test_embedded_credentials_never_verified(self):
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://user:pass@ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(True)
        assert not _is_verified_dashboard(page)

    def test_subdomain_lookalike_never_verified(self):
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com.evil.example/#!dashboard")
        page.set_dashboard_ready(True)
        assert not _is_verified_dashboard(page)


class TestProductsRedirectBackToDashboard:
    def test_products_to_dashboard(self):
        settings = make_test_settings()
        page = FakePage(url="https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings,
            page=page,
            otp_provider=otp_provider,
            clock=FakeClock(),
        )

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        # The dashboard is reached by clicking the real menu item, never by a
        # direct goto to the dashboard URL.
        assert page.goto_calls == []
        assert page.menu_clicks >= 1

    def test_navigating_to_dashboard_after_reload(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        monitor.monitor_once()

        assert page.url == settings.cmp_dashboard_url
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)
        assert page.menu_clicks >= 1

    def test_products_after_reload_navigates_back_without_relogin(self):
        """Reload redirecting to products is a normal redirect: back to dashboard, no OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)

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
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)
        assert page.menu_clicks >= 1


class TestSessionExpiryTriggeringReLogin:
    def test_auth_expired_triggers_relogin(self):
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should return True to indicate re-auth performed
        assert result is True
        # Should have requested exactly one OTP (no double polling)
        assert len(otp_provider.poll_calls) == 1
        # Should have navigated to dashboard after relogin
        assert page.url == settings.cmp_dashboard_url
        # Re-login ends on products, then the real Dashboard menu is clicked
        assert page.menu_clicks >= 1
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)

    def test_new_otp_timestamp_on_every_relogin(self):
        """Each re-login must read a fresh timestamp from the clock."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        monitor.monitor_once()
        first = otp_provider.poll_calls[0]
        assert first.tzinfo is not None

        # Simulate another session expiry
        page.set_session_expired()
        monitor.monitor_once()

        second = otp_provider.poll_calls[1]
        assert first != second

    def test_false_session_closed_redirect_settles_to_products_skips_relogin(self):
        """When /session-closed redirects back to products within the settle window, OTP is skipped."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed?locale=en")
        page.set_menu_visible(True)
        # Configure pending transition: after 2 URL reads, transition to products
        page._pending = {"remaining": 2, "url": settings.cmp_products_url, "state": "products"}

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should return False (meaning navigated from products to dashboard without relogin)
        assert result is False
        # OTP provider should NOT have been called
        assert len(otp_provider.poll_calls) == 0
        # Should have reached dashboard
        assert page.url == settings.cmp_dashboard_url

    def test_transient_unknown_route_during_settle_does_not_call_otp_provider(self):
        """When auth-expired settles to an UNKNOWN route, recovery is called without requesting OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed")
        page._pending = {"remaining": 1, "url": "https://ep.iotcc.telkomsel.com/unknown", "state": "initial"}
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should NOT call OTP provider
        assert len(otp_provider.poll_calls) == 0
        # Should have performed recovery to dashboard
        assert result is True

    def test_route_settles_to_verified_dashboard_skips_otp(self):
        """When auth-expired settles to a verified dashboard state, OTP is skipped."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed")
        page._pending = {"remaining": 1, "url": settings.cmp_dashboard_url, "state": "dashboard"}
        page.set_menu_visible(True)
        page.set_dashboard_ready(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        assert result is False
        assert len(otp_provider.poll_calls) == 0

    def test_persistent_auth_expiry_invokes_full_relogin_exactly_once(self, monkeypatch):
        """Persistent AUTH_EXPIRED route invokes the full re-login path exactly once."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed?locale=en")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock()
        )

        relogin_calls = []
        original_relogin = monitor._relogin_and_navigate

        def spy_relogin():
            relogin_calls.append(1)
            return original_relogin()

        monkeypatch.setattr(monitor, "_relogin_and_navigate", spy_relogin)

        result = monitor.monitor_once()
        assert result is True
        assert len(relogin_calls) == 1
        assert len(otp_provider.poll_calls) == 1

    def test_unverified_dashboard_during_settle_triggers_recovery_and_no_otp(self):
        """When auth-expired settles to a persistent #!dashboard URL with unverified DOM,
        it does NOT return settled Dashboard success and does NOT request OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/session-closed")
        page._pending = {"remaining": 2, "url": settings.cmp_dashboard_url, "state": "dashboard"}
        page.set_dashboard_ready(False)  # DOM is NOT ready!
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Should NOT return False (which would claim settled Dashboard success)
        assert result is True  # Recovered via bounded recovery (stage through products -> SPA menu click)
        # Should NOT request OTP merely because URL reports #!dashboard
        assert len(otp_provider.poll_calls) == 0
        # Reached dashboard via real recovery
        assert page.url == settings.cmp_dashboard_url


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
        page.set_menu_visible(True)
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

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
        page.set_menu_visible(True)

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
        page.set_menu_visible(True)

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())
        result = monitor.monitor_once()

        # Post-reload unknown state is handled in the same cycle via bounded
        # recovery (which returns True on success).
        assert result is True
        assert monitor._consecutive_recoveries == 0
        assert page.url == settings.cmp_dashboard_url

    def test_successful_unknown_state_recovery(self):
        settings = make_test_settings()
        page = FakePage("https://unknown.com")
        page.set_menu_visible(True)

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
        def failing_goto(url, timeout=None, wait_until=None):
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
        def goto_wrong_url(url, timeout=None, wait_until=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"  # Not dashboard
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # Should raise because recovery didn't reach dashboard
        with pytest.raises(RecoveryError, match="could not verify dashboard"):
            monitor.monitor_once()
        # Counter should NOT be reset
        assert monitor._consecutive_recoveries == 1

    def test_exhaustion_at_configured_limit(self):
        settings = make_test_settings(recovery_retry_limit=2)
        page = FakePage("https://unknown.com")

        # Navigation succeeds but ends up at non-dashboard URL
        def goto_wrong_url(url, timeout=None, wait_until=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError, RecoveryExhaustedError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt: recovery fails, counter=1
        with pytest.raises(RecoveryError, match="could not verify dashboard"):
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
        page.set_menu_visible(True)

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
        """Dashboard reload -> unknown -> bounded recovery back to dashboard."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)

        # Reload goes to unknown state
        def reload_to_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_to_unknown

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)
        result = monitor.monitor_once()

        # Post-reload unknown is handled in the same cycle via bounded recovery
        # (no re-authentication).
        assert result is True
        assert monitor._consecutive_recoveries == 0
        assert page.url == settings.cmp_dashboard_url
        # Backoff sleep happened before the recovery navigation
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
        def failing_goto(url, timeout=None, wait_until=None):
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
        def goto_wrong_url(url, timeout=None, wait_until=None):
            page._url = "https://ep.iotcc.telkomsel.com/#!other"
            page._navigations.append(url)
        page.goto = goto_wrong_url

        from dashboard_monitor import ContinuousMonitor, RecoveryError, RecoveryExhaustedError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # First attempt: recovery fails, counter=1
        with pytest.raises(RecoveryError, match="could not verify dashboard"):
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
        page.set_menu_visible(True)

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
        page.set_menu_visible(True)

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
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        # It must recover through the products URL (SPA shell) and then the
        # Dashboard menu click - never authenticate directly and never goto
        # the dashboard URL directly.
        assert monitor.monitor_once() is True
        assert page.url == settings.cmp_dashboard_url
        assert monitor._consecutive_recoveries == 0
        assert len(otp_provider.poll_calls) == 0
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)


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

        # The page is still on a constructor-ready dashboard, so recovery
        # verifies it without any navigation.
        assert monitor.monitor_once() is True
        assert monitor._consecutive_recoveries == 0
        assert settings.recovery_backoff_seconds in clock.sleep_calls
        assert page.goto_calls == []


class TestDashboardMenuNavigation:
    """SPA menu-click navigation from products/dashboard to the dashboard state."""

    def test_navigate_to_dashboard_uses_menu_click(self):
        """When the SPA menu is visible, navigate_to_dashboard clicks it and does not goto."""
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        assert page.goto_calls == []
        assert page.menu_clicks >= 1

    def test_navigate_to_dashboard_waits_for_slow_vaadin_bootstrap(self):
        """Observed live: Vaadin 7 menu takes ~65s to render after domcontentloaded.

        The ``_wait_for_vaadin_loading`` pre-wait must wait for the menu to appear
        (simulated via ``set_menu_visible_after_reads``) rather than timing out
        before the menu loads. The click/verify deadline then proceeds normally.
        """
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = VaadinBootingFakePage("https://ep.iotcc.telkomsel.com/#!products")
        # Menu renders only after several visibility polls (slow Vaadin bootstrap).
        page.set_menu_visible_after_reads(5)
        page.set_dashboard_ready(True)

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        assert page.goto_calls == []
        assert page.menu_clicks >= 1

    def test_navigate_to_dashboard_menu_never_visible_raises(self):
        """If the menu never renders, navigation must fail instead of goto dashboard."""
        from dashboard_monitor import navigate_to_dashboard, RecoveryError

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        # Menu never becomes visible -> polling times out.

        with pytest.raises(RecoveryError):
            navigate_to_dashboard(page, settings, FakeClock())
        # Never a direct navigation to the dashboard URL.
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)
        assert page.menu_clicks == 0

    def test_monitor_once_products_menu_click_no_new_otp(self):
        """Products redirect uses the menu click to return to dashboard without a new OTP."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock()
        )

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert len(otp_provider.poll_calls) == 0
        assert page.goto_calls == []
        assert page.menu_clicks >= 1

    def test_recovery_uses_menu_click_when_visible(self):
        """Unknown-state recovery stages through products and uses the menu click."""
        settings = make_test_settings()
        page = FakePage("https://unknown.com")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock()
        )

        assert monitor.monitor_once() is True
        assert page.url == settings.cmp_dashboard_url
        # Recovery goes to the products URL (to load the SPA shell), then the
        # real Dashboard menu item - never directly to the dashboard URL.
        assert page.goto_calls == [
            (settings.cmp_products_url, settings.navigation_timeout_ms, "domcontentloaded")
        ]
        assert page.menu_clicks >= 1
        assert len(otp_provider.poll_calls) == 0

    def test_menu_click_failure_raises_recovery_error(self):
        """If the menu is not visible, navigation raises RecoveryError (no goto fallback)."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")

        from dashboard_monitor import navigate_to_dashboard, RecoveryError
        with pytest.raises(RecoveryError):
            navigate_to_dashboard(page, settings, FakeClock())
        assert page.goto_calls == []
        assert page.url == settings.cmp_products_url

    def test_reload_to_products_navigates_immediately_via_menu_click(self):
        """Post-reload reset to products navigates back to dashboard immediately
        via the SPA menu click within the same monitor cycle."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)

        def reload_to_products():
            page._url = "https://ep.iotcc.telkomsel.com/#!products"
        page.reload = reload_to_products

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=clock
        )

        result = monitor.monitor_once()
        # No re-authentication: immediate navigation back to dashboard.
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert page.goto_calls == []  # menu click used, no direct goto
        assert page.menu_clicks >= 1
        assert len(otp_provider.poll_calls) == 0


class TestDashboardVerification:
    """Regression tests for the false-positive dashboard detection fix."""

    def test_hidden_menu_is_waited_for_not_goto(self):
        """A not-yet-rendered Dashboard menu is waited for, never bypassed."""
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible_after_reads(3)  # menu renders after 3 polls

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks >= 1
        assert page.goto_calls == []

    def test_delayed_menu_becomes_visible_and_clicked(self):
        """A delayed Dashboard menu becomes visible and is clicked."""
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible_after_reads(2)

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.menu_clicks == 1
        assert page.url == settings.cmp_dashboard_url

    def test_no_production_path_calls_goto_dashboard_url(self):
        """No production path may navigate directly to the dashboard URL."""
        import inspect
        import dashboard_monitor

        src = inspect.getsource(dashboard_monitor)
        assert "goto(settings.cmp_dashboard_url" not in src
        assert "goto(self._settings.cmp_dashboard_url" not in src

        # Behavioral check: recovery from unknown never goto's the dashboard URL.
        settings = make_test_settings()
        page = FakePage("https://unknown.com")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=FakeOtpProvider(), clock=FakeClock()
        )
        assert monitor.monitor_once() is True
        assert page.url == settings.cmp_dashboard_url
        assert all("!dashboard" not in url for url, _, _ in page.goto_calls)

    def test_temporary_dashboard_url_that_resets_is_not_successful(self):
        """A stale #!dashboard URL that later resets to #!products is not success."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(False)  # URL reports dashboard but UI is not there
        page.set_menu_visible(True)
        page.set_post_reload("https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=3)
        page.reload()

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        result = monitor.monitor_once()
        # The stale URL was not accepted: the monitor detected the products
        # reset and re-navigated through the real menu click.
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks >= 1
        assert len(otp_provider.poll_calls) == 0

    def test_dashboard_success_requires_consecutive_verified_polls(self):
        """A menu click that bounces back to products is retried, not reported as success."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        page.set_menu_click_bounce(
            "https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=2, via_dashboard=True
        )

        from dashboard_monitor import navigate_to_dashboard
        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        # The first click bounced back to products, so exactly one retry (a
        # second real click) was required - never more.
        assert page.menu_clicks == 2

    def test_post_click_unverified_diagnostics_are_emitted(self, caplog):
        """Throttled DEBUG diagnostics are emitted while verification is pending.

        Regression test for the live failure where the Dashboard menu was
        clicked but verification timed out with NO diagnostics: the post-click
        diagnostics must be emitted (safe facts only) while the route has not
        yet confirmed the dashboard, plus one final diagnostic before timeout.
        """
        import logging

        from dashboard_monitor import navigate_to_dashboard, RecoveryError

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        # Model a real-world transition that takes far longer than the
        # navigation timeout: the URL/hash/DOM stay on Products after the click.
        page.set_click_transition_delay(100000)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RecoveryError):
                navigate_to_dashboard(page, settings, FakeClock())

        assert "Dashboard unverified after click" in caplog.text
        assert "Dashboard DOM diagnostics" in caplog.text
        assert "Dashboard verification timed out" in caplog.text
        # Safe facts only: no HTML dump, credentials, OTPs or full query URLs.

    def test_monitor_once_on_closed_page_raises_sanitized_recovery_error(self, caplog):
        settings = make_test_settings()

        class ClosedPage:
            @property
            def url(self):
                raise RuntimeError("Target page, context or browser has been closed")

            def goto(self, *args, **kwargs):
                raise RuntimeError("Target page, context or browser has been closed")

        from dashboard_monitor import ContinuousMonitor, RecoveryError
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=ClosedPage(), otp_provider=otp_provider, clock=FakeClock()
        )

        with pytest.raises(RecoveryError) as exc_info:
            monitor.monitor_once()

        assert "Target page" not in str(exc_info.value)
        assert str(exc_info.value) == "Dashboard navigation failed"
        assert "password" not in caplog.text.lower()
        assert "secret" not in caplog.text.lower()

    def test_products_reached_by_manual_url_change_detected_within_one_second(self):
        """A manual URL change to products is detected at the next one-second poll."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)
        # The transition fires during the first one-second monitor poll
        # (after_reads=2: one read by monitor_once's classify, one by the poll).
        page.schedule_transition(
            "https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=2
        )

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert len(otp_provider.poll_calls) == 0
        # 1 wait poll + 2 consecutive verification polls
        assert clock.sleep_calls.count(1.0) == 3

    def test_session_closed_triggers_relogin_immediately(self):
        """/session-closed while monitoring triggers an immediate fresh relogin."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)
        page.schedule_transition(
            "https://ep.iotcc.telkomsel.com/session-closed?locale=en", "cas", after_reads=1
        )

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        clock = FakeClock()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=clock)

        result = monitor.monitor_once()
        assert result is True
        assert len(otp_provider.poll_calls) == 1
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks >= 1

    def test_menu_wait_detects_session_expiry_triggers_relogin(self):
        """If the page hits CAS/session-closed while waiting for the menu, re-login."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        page.schedule_transition(
            "https://ep.iotcc.telkomsel.com/session-closed?locale=en", "cas", after_reads=1
        )

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        result = monitor.monitor_once()
        assert result is True
        assert len(otp_provider.poll_calls) == 1
        assert page.url == settings.cmp_dashboard_url

    def test_relogin_ends_with_products_then_menu_click(self):
        """Re-login completes on products; the dashboard is reached by menu click."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/cas/login")
        page.set_menu_visible(True)

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        result = monitor.monitor_once()
        assert result is True
        assert page.menu_clicks >= 1
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)
        assert page.url == settings.cmp_dashboard_url

    def test_post_reload_delayed_dashboard_to_products_detected_same_cycle(self):
        """Post-reload DASHBOARD -> PRODUCTS delayed transition is handled in the same cycle."""
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)
        page.set_post_reload(
            "https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=2
        )
        page.reload()

        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=otp_provider, clock=FakeClock())

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks >= 1
        assert len(otp_provider.poll_calls) == 0


class TestSteadyStateVerification:
    """Review findings: verified-UI steady-state checks and click discipline."""

    def test_dashboard_url_with_unverified_ui_detected_within_one_second(self):
        """A #!dashboard URL whose UI is unverified is detected within one second."""
        settings = make_test_settings(refresh_interval_seconds=60)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(False)  # URL says dashboard, DOM does not confirm
        page.set_menu_visible(True)
        clock = FakeClock()
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=clock
        )

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks >= 1
        assert len(otp_provider.poll_calls) == 0
        # Recovery happened well before the refresh deadline: the monitor did
        # NOT wait the full refresh interval before acting on the mismatch.
        assert len(clock.sleep_calls) < settings.refresh_interval_seconds

    def test_two_consecutive_failed_verifications_trigger_recovery(self):
        """Exactly two consecutive unverified polls trigger recovery."""
        settings = make_test_settings(refresh_interval_seconds=60)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(False)  # never verified
        page.set_menu_visible(True)
        clock = FakeClock()
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=clock
        )

        result = monitor.monitor_once()
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert page.menu_clicks == 1
        assert len(otp_provider.poll_calls) == 0
        # 2 monitor polls (1s each) confirmed the inconsistency, then the
        # inconsistent-dashboard observer ran for its bounded period (2 polls),
        # then 2 verification polls confirmed the recovered dashboard.
        assert clock.sleep_calls.count(1.0) == 6

    def test_slow_menu_click_transition_causes_only_one_click(self):
        """A slow menu-click transition (delayed render) causes only one click."""
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        page.set_click_delay_reads(3)  # dashboard view renders only after 3 reads

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        # Exactly one click: the slow transition is waited for, not re-clicked.
        assert page.menu_clicks == 1

    def test_delayed_menu_click_transition_produces_exactly_one_click(self):
        """A click whose URL/hash AND DOM transition are delayed causes one click.

        Models the real-world Vaadin transition where clicking Dashboard does
        not change either the URL or the DOM immediately: both stay on products
        for several polls, then transition together to the dashboard. The
        in-flight click must NOT be repeated every second while the first
        navigation is still loading.
        """
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        # URL/hash AND dashboard DOM change only after 4 url reads (a slow
        # real-world Vaadin transition).
        page.set_click_transition_delay(4)

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        # Exactly one click: the delayed transition is waited for, never
        # re-clicked merely because the Products URL is still unchanged.
        assert page.menu_clicks == 1

    def test_35_second_delayed_transition_produces_exactly_one_click(self):
        """A click whose URL/hash AND DOM transition are delayed for >=35 seconds causes one click.

        Models a slow real-world Vaadin transition where clicking Dashboard
        does not change either the URL or the DOM for at least 35 simulated
        seconds after the click. The transition to dashboard happens only
        after this delay. The in-flight click must NOT be repeated every
        second while the first navigation is still loading.

        This test uses a clock where time advances through sleep() calls,
        ensuring the simulated 35+ second delay is realistic.
        """
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings(navigation_timeout_ms=120000)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        # 35 seconds = 35 iterations at 1s sleep each = 70 URL reads
        # (2 reads per iteration: classify_state + _is_verified_dashboard)
        page.set_click_transition_delay(70)

        # Use a clock that advances time through sleep() calls
        class FakeClockAdvancingSleep(FakeClock):
            def sleep(self, seconds):
                super().sleep(seconds)
                self._t += seconds

        result = navigate_to_dashboard(page, settings, FakeClockAdvancingSleep())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        # Exactly one click: the delayed transition is waited for, never
        # re-clicked merely because the Products URL is still unchanged.
        assert page.menu_clicks == 1

    def test_confirmed_bounce_permits_exactly_one_retry(self):
        """A confirmed bounce (left Products, then returned) permits one retry.

        The page first left Products (the click was consumed) and later came
        back to Products: the router undid the navigation. This round-trip is
        the positive evidence required for a single re-click - never more.
        """
        from dashboard_monitor import navigate_to_dashboard

        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_menu_visible(True)
        page.set_menu_click_bounce(
            "https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=2, via_dashboard=True
        )

        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        # First click bounced back to products, so exactly one retry (a second
        # real click) was required - never more.
        assert page.menu_clicks == 2

    def test_transient_unknown_after_reload_no_immediate_recovery(self):
        """A transient UNKNOWN right after reload does not trigger immediate recovery."""
        settings = make_test_settings(refresh_interval_seconds=60)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)

        # Reload lands on an unknown URL for one poll, then settles on products.
        def reload_transient_unknown():
            page._url = "https://unknown.com"
            page.schedule_transition(
                "https://ep.iotcc.telkomsel.com/#!products", "products", after_reads=2
            )
        page.reload = reload_transient_unknown

        clock = FakeClock()
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=clock
        )

        result = monitor.monitor_once()
        # The transient UNKNOWN was confirmed before recovering: the page
        # settled on products and was navigated back via the real menu click,
        # with NO bounded recovery and NO new OTP.
        assert result is False
        assert page.url == settings.cmp_dashboard_url
        assert monitor._consecutive_recoveries == 0
        assert len(otp_provider.poll_calls) == 0
        assert settings.recovery_backoff_seconds not in clock.sleep_calls
        assert page.goto_calls == []

    def test_stable_unknown_after_reload_triggers_bounded_recovery(self):
        """A stable UNKNOWN after reload does trigger bounded recovery."""
        settings = make_test_settings(refresh_interval_seconds=60)
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_menu_visible(True)

        def reload_stable_unknown():
            page._url = "https://unknown.com"
        page.reload = reload_stable_unknown

        clock = FakeClock()
        from dashboard_monitor import ContinuousMonitor
        otp_provider = FakeOtpProvider()
        monitor = ContinuousMonitor(
            settings=settings, page=page, otp_provider=otp_provider, clock=clock
        )

        result = monitor.monitor_once()
        assert result is True
        assert page.url == settings.cmp_dashboard_url
        assert monitor._consecutive_recoveries == 0  # reset after verified dashboard
        assert settings.recovery_backoff_seconds in clock.sleep_calls
        assert len(otp_provider.poll_calls) == 0


class TestLiveFragmentVerification:
    """Tests for the live window.location.hash verification (Firefox page.url lag fix)."""

    def test_classify_state_uses_live_hash_when_url_lags(self):
        """classify_state returns DASHBOARD when page.url fragment is !products but live hash is #!dashboard."""
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_hash("#!dashboard")  # Live hash is dashboard, URL lags
        assert classify_state(page) == DashboardState.DASHBOARD

    def test_classify_state_products_when_both_url_and_hash_agree(self):
        """classify_state returns PRODUCTS when both URL and live hash are #!products."""
        from dashboard_monitor import classify_state, DashboardState
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_hash("#!products")
        assert classify_state(page) == DashboardState.PRODUCTS

    def test_is_verified_dashboard_true_when_live_hash_dashboard_dom_ready(self):
        """_is_verified_dashboard is True when live hash is #!dashboard and DOM ready, even if URL fragment is !products."""
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_hash("#!dashboard")  # Live hash is dashboard
        page.set_dashboard_ready(True)  # DOM confirms dashboard
        assert _is_verified_dashboard(page) is True

    def test_is_verified_dashboard_false_when_hash_dashboard_but_dom_not_ready(self):
        """_is_verified_dashboard is False when live hash is #!dashboard but DOM not ready (false positive protection)."""
        from dashboard_monitor import _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com/#!products")
        page.set_hash("#!dashboard")  # Live hash is dashboard
        page.set_dashboard_ready(False)  # DOM does NOT confirm dashboard
        assert _is_verified_dashboard(page) is False

    def test_navigate_to_dashboard_succeeds_with_live_hash_and_one_click(self):
        """navigate_to_dashboard returns True with exactly one click when live hash and DOM confirm dashboard.

        Models the real Firefox failure: after the menu click, ``window.location.hash``
        and the DOM already confirm Dashboard while ``page.url`` still lags at
        ``#!products``. Verification must succeed from the live signals.
        """
        from dashboard_monitor import navigate_to_dashboard

        class _LaggedUrlMenuLocator:
            def __init__(self, page):
                self._page = page

            @property
            def first(self):
                return self

            def is_visible(self):
                return True

            def inner_text(self):
                return "Dashboard"

            def click(self):
                self._page._menu_clicks += 1
                # Live hash and DOM confirm dashboard immediately, but the
                # page.url fragment intentionally stays at #!products (Firefox
                # Playwright lag during the Vaadin route transition).
                self._page._hash_override = "#!dashboard"
                self._page._dashboard_ready = True

        class _LaggedUrlPage(FakePage):
            def __init__(self, url):
                super().__init__(url)
                self._menu_visible = True
                self._dashboard_ready = False

            def locator(self, selector, has_text=None):
                if selector == "span.main-menu-item-caption":
                    return _LaggedUrlMenuLocator(self)
                return super().locator(selector, has_text=has_text)

        settings = make_test_settings()
        page = _LaggedUrlPage("https://ep.iotcc.telkomsel.com/#!products")
        result = navigate_to_dashboard(page, settings, FakeClock())
        assert result is True
        # The click happened once; verification came from the live hash + DOM.
        assert page.menu_clicks == 1
        assert not any("!dashboard" in url for url, _, _ in page.goto_calls)
        # The page.url may still lag, but the live hash confirmed dashboard.
        assert page._hash_override == "#!dashboard"

    def test_goto_dashboard_false_positive_still_protected(self):
        """Direct goto to #!dashboard changes URL but DOM never ready - still not verified (existing guard)."""
        from dashboard_monitor import _is_verified_dashboard
        settings = make_test_settings()
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.goto(settings.cmp_dashboard_url, timeout=1000, wait_until="commit")
        assert page.url == settings.cmp_dashboard_url
        # Even if we set the live hash to dashboard, DOM is not ready
        page.set_hash("#!dashboard")
        page.set_dashboard_ready(False)
        assert _is_verified_dashboard(page) is False

    def test_classify_state_not_dashboard_when_stale_url_dashboard_and_empty_live_hash(self):
        """classify_state returns PRODUCTS/UNKNOWN when page.url fragment is !dashboard but live hash is empty.

        The live hash is authoritative when evaluate is available. An empty live hash
        should not fall back to the stale page.url fragment.
        Also verifies _is_verified_dashboard is False even when DOM is ready.
        """
        from dashboard_monitor import classify_state, DashboardState, _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_hash("")  # Live hash is empty (authoritative)
        page.set_dashboard_ready(True)  # DOM is ready
        # Should NOT be DASHBOARD because live hash is empty
        assert classify_state(page) != DashboardState.DASHBOARD
        # Full verifier contract: _is_verified_dashboard must also be False
        assert _is_verified_dashboard(page) is False

    def test_classify_state_not_dashboard_when_stale_url_dashboard_and_failed_evaluation(self, caplog):
        """classify_state returns UNKNOWN when page.url fragment is !dashboard but evaluate fails.

        Evaluation exception should produce empty live hash, not fall back to page.url.
        Also verifies _is_verified_dashboard is False even when DOM is ready,
        and that no raw exception text is exposed through public behavior or logs.
        """
        import logging
        from dashboard_monitor import classify_state, DashboardState, _is_verified_dashboard

        raw_message = "Target page, context or browser has been closed: SECRET_RAW_ERROR"

        class FailingEvaluatePage(FakePage):
            def evaluate(self, expr):
                raise RuntimeError(raw_message)

        page = FailingEvaluatePage("https://ep.iotcc.telkomsel.com/#!dashboard")
        page.set_dashboard_ready(True)  # DOM is ready
        # Result should be a safe DashboardState enum, not an exception
        result = classify_state(page)
        assert result == DashboardState.UNKNOWN
        # Full verifier contract: _is_verified_dashboard must also be False
        assert _is_verified_dashboard(page) is False
        # Sanitization: raw exception message must not leak through public result
        assert raw_message not in str(result)
        # Sanitization: raw exception message must not leak through captured logs
        caplog.set_level(logging.DEBUG)
        classify_state(page)
        log_output = "".join(caplog.messages)
        assert raw_message not in log_output

    def test_classify_state_dashboard_when_root_url_no_fragment_and_live_hash_dashboard(self):
        """classify_state returns DASHBOARD when page.url has no fragment (root portal) but live hash is #!dashboard.

        This is the observed real case: Firefox lands at https://ep.iotcc.telkomsel.com/
        (no fragment) while window.location.hash is already #!dashboard.
        Also verifies _is_verified_dashboard is True when DOM is ready.
        """
        from dashboard_monitor import classify_state, DashboardState, _is_verified_dashboard
        page = FakePage("https://ep.iotcc.telkomsel.com/")  # No fragment
        page.set_hash("#!dashboard")  # Live hash is dashboard
        page.set_dashboard_ready(True)  # DOM is ready
        assert classify_state(page) == DashboardState.DASHBOARD
        # Full verifier contract: _is_verified_dashboard must also be True
        assert _is_verified_dashboard(page) is True
