"""Tests for main entry point - all offline."""

import importlib
import sys
from unittest.mock import MagicMock, patch

from config import Settings, SecretValue

# Complete, valid environment used to run main() offline. All secrets are
# test-only placeholders; nothing here touches production systems.
TEST_ENV = {
    "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
    "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
    "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
    "CMP_USERNAME": "test_user",
    "CMP_PASSWORD": "test_pass",
    "IMAP_USERNAME": "imap_user",
    "IMAP_PASSWORD": "imap_pass",
    "IMAP_HOST": "mail.gmf-aeroasia.co.id",
    "IMAP_PORT": "993",
    "IMAP_TLS_MODE": "imaps",
    "IMAP_VERIFY_TLS": "true",
    "IMAP_MAILBOX": "INBOX",
    "OTP_SUBJECT": "CMP - YOUR TOKEN",
    "RUN_START_TIMEZONE": "Asia/Jakarta",
    "HEADLESS": "false",
    "LOG_LEVEL": "INFO",
}


def make_test_settings() -> Settings:
    return Settings(
        cas_url="https://ep.iotcc.telkomsel.com/cas/login",
        cmp_products_url="https://ep.iotcc.telkomsel.com/#!products",
        cmp_dashboard_url="https://ep.iotcc.telkomsel.com/#!dashboard",
        cmp_username=SecretValue("test_user"),
        cmp_password=SecretValue("test_pass"),
        imap_host="mail.gmf-aeroasia.co.id",
        imap_port=993,
        imap_username=SecretValue("imap_user"),
        imap_password=SecretValue("imap_pass"),
        imap_tls_mode="imaps",
        imap_verify_tls=True,
        imap_mailbox="INBOX",
        otp_subject="CMP - YOUR TOKEN",
        otp_poll_interval_seconds=2,
        otp_timeout_seconds=120,
        run_start_timezone="Asia/Jakarta",
        browser_timeout_ms=30000,
        navigation_timeout_ms=30000,
        otp_form_timeout_ms=5000,
        refresh_interval_seconds=60,
        recovery_retry_limit=3,
        recovery_backoff_seconds=5,
        headless=False,
        runtime_artifact_dir=None,
        browser_storage_state_path=None,
        log_level="INFO",
    )


class TestNoBrowserDuringImport:
    def test_main_module_import(self):
        """Importing main should not launch a browser."""
        import main
        assert callable(main.main)

    def test_import_does_not_launch_browser(self):
        """Importing main must not launch Playwright or a browser."""
        import main
        # Re-execute the module while sync_playwright is mocked. If module-level
        # code launched a browser it would call sync_playwright() here.
        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            importlib.reload(main)
            mock_sync.assert_not_called()


class TestConfigurationLoading:
    def test_invalid_config_returns_one(self):
        import main

        # Deterministic invalid configuration: non-HTTPS CAS URL.
        bad_env = dict(TEST_ENV)
        bad_env["CMP_CAS_URL"] = "http://ep.iotcc.telkomsel.com/cas/login"
        result = main.main(env=bad_env)
        assert result == 1


class TestFirefoxOnly:
    def test_firefox_requested(self):
        """Verify Firefox is requested via playwright.firefox.launch."""
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            # Need to mock ImapClient and authenticate_cmp to avoid actual execution
            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp"):
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = [False, KeyboardInterrupt()]
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        main.main(env=TEST_ENV)

                        # Verify Firefox was launched
                        mock_playwright.firefox.launch.assert_called_once()
                        # Verify Chromium/Chrome/WebKit were NOT called
                        assert not mock_playwright.chromium.launch.called
                        assert not mock_playwright.webkit.launch.called


class TestPlaywrightStopInvoked:
    def test_playwright_stop_on_exit(self):
        """Verify playwright.stop() is called on exit."""
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp"):
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = [False, KeyboardInterrupt()]
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        main.main(env=TEST_ENV)

                        # Verify playwright.stop() was called
                        mock_playwright.stop.assert_called_once()


class TestCleanupOrder:
    def test_cleanup_order_page_context_browser_playwright_imap(self):
        """Verify resources are cleaned up in correct order."""
        cleanup_order = []

        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            def track_page_close():
                cleanup_order.append("page")
            def track_context_close():
                cleanup_order.append("context")
            def track_browser_close():
                cleanup_order.append("browser")
            def track_playwright_stop():
                cleanup_order.append("playwright")
            def track_imap_disconnect():
                cleanup_order.append("imap")

            mock_page.close.side_effect = track_page_close
            mock_context.close.side_effect = track_context_close
            mock_browser.close.side_effect = track_browser_close
            mock_playwright.stop.side_effect = track_playwright_stop

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap.disconnect.side_effect = track_imap_disconnect
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp"):
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = [False, KeyboardInterrupt()]
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        main.main(env=TEST_ENV)

                        # Verify cleanup order: page -> context -> browser -> playwright -> imap
                        expected_order = ["page", "context", "browser", "playwright", "imap"]
                        assert cleanup_order == expected_order


class TestCleanupAfterPartialInitialization:
    def test_cleanup_on_imap_connect_failure(self):
        """Verify IMAP is cleaned up if connect fails."""
        with patch("main.ImapClient") as mock_imap_class:
            mock_imap = MagicMock()
            mock_imap.connect.side_effect = RuntimeError("IMAP connection failed")
            mock_imap_class.return_value = mock_imap

            import main
            result = main.main(env=TEST_ENV)

            # Should return error code
            assert result == 1
            # IMAP disconnect should still be called
            mock_imap.disconnect.assert_called_once()

    def test_cleanup_on_browser_launch_failure(self):
        """Verify cleanup if browser launch fails."""
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_playwright.firefox.launch.side_effect = RuntimeError("Browser launch failed")
            mock_sync.return_value.start.return_value = mock_playwright

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                import main
                result = main.main(env=TEST_ENV)

                # Should return error code
                assert result == 1
                # Playwright should be stopped
                mock_playwright.stop.assert_called_once()
                # IMAP should be disconnected
                mock_imap.disconnect.assert_called_once()


class TestGracefulShutdown:
    def test_keyboard_interrupt_returns_zero(self):
        """Test that KeyboardInterrupt returns 0."""
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp"):
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = KeyboardInterrupt()
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        result = main.main(env=TEST_ENV)

                        assert result == 0


class TestRecoveryExhaustedReturnsError:
    def test_recovery_exhausted_returns_one(self):
        """Test that RecoveryExhaustedError returns 1."""
        from dashboard_monitor import RecoveryExhaustedError

        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp"):
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = RecoveryExhaustedError("Max retries")
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        result = main.main(env=TEST_ENV)

                        assert result == 1


class TestAuthenticationErrorReturnsError:
    def test_auth_error_returns_one(self):
        """Test that AuthenticationError returns 1."""
        from cmp_auth import AuthenticationError

        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            with patch("main.ImapClient") as mock_imap_class:
                mock_imap = MagicMock()
                mock_imap_class.return_value = mock_imap

                with patch("main.authenticate_cmp", side_effect=AuthenticationError("Auth failed")):
                    import main
                    result = main.main(env=TEST_ENV)

                    assert result == 1
