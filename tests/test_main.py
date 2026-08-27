"""Tests for main entry point - all offline."""

import importlib
from unittest.mock import MagicMock, patch

from config import SecretValue, Settings

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
    "IMAP_HOST": "mail.company.local",
    "IMAP_PORT": "993",
    "IMAP_TLS_MODE": "imaps",
    "IMAP_VERIFY_TLS": "true",
    "IMAP_MAILBOX": "INBOX",
    "OTP_SUBJECT": "CMP - YOUR TOKEN",
    "RUN_START_TIMEZONE": "Asia/Jakarta",
    "HEADLESS": "false",
    "LOG_LEVEL": "INFO",
    "WARP_MODE": "warp",
}


def make_test_settings() -> Settings:
    return Settings(
        cas_url="https://ep.iotcc.telkomsel.com/cas/login",
        cmp_products_url="https://ep.iotcc.telkomsel.com/#!products",
        cmp_dashboard_url="https://ep.iotcc.telkomsel.com/#!dashboard",
        cmp_username=SecretValue("test_user"),
        cmp_password=SecretValue("test_pass"),
        imap_host="mail.company.local",
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
        otp_clock_skew_tolerance_seconds=120,
        refresh_interval_seconds=60,
        recovery_retry_limit=3,
        recovery_backoff_seconds=5,
        headless=False,
        firefox_executable_path=None,
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
    @patch("main.navigate_to_dashboard")
    def test_firefox_requested(self, mock_navigate):
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
                        mock_playwright.firefox.launch.assert_called_once_with(headless=False)
                        # Verify browser context was created with 1920x1080 viewport

                        mock_browser.new_context.assert_called_once_with(viewport={"width": 1920, "height": 1080})
                        # Verify Chromium/Chrome/WebKit were NOT called
                        assert not mock_playwright.chromium.launch.called
                        assert not mock_playwright.webkit.launch.called

    @patch("main.navigate_to_dashboard")
    def test_firefox_launched_with_socks5_proxy_in_proxy_mode(self, mock_navigate):
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
                        env = dict(TEST_ENV)
                        env["WARP_MODE"] = "proxy"
                        env["WARP_PROXY_PORT"] = "40000"
                        main.main(env=env)

                        mock_playwright.firefox.launch.assert_called_once_with(
                            headless=False,
                            proxy={"server": "socks5://127.0.0.1:40000"}
                        )


class TestInstalledFirefoxExecutable:
    @patch("main.navigate_to_dashboard")
    def test_configured_executable_path_is_passed_to_firefox(self, mock_navigate, tmp_path):
        executable = tmp_path / "firefox.exe"
        executable.write_text("test executable")
        env = dict(TEST_ENV)
        env["FIREFOX_EXECUTABLE_PATH"] = str(executable)
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_sync.return_value.start.return_value = mock_playwright
            mock_playwright.firefox.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page
            with patch("main.ImapClient") as mock_imap_class, patch("main.authenticate_cmp"), patch("main.ContinuousMonitor") as monitor_class:
                mock_imap_class.return_value = MagicMock()
                monitor_class.return_value.monitor_once.side_effect = KeyboardInterrupt()
                import main
                main.main(env=env)
        mock_playwright.firefox.launch.assert_called_once_with(
            headless=False, executable_path=str(executable.resolve())
        )


class TestPlaywrightStopInvoked:
    @patch("main.navigate_to_dashboard")
    def test_playwright_stop_on_exit(self, mock_navigate):
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
    @patch("main.navigate_to_dashboard")
    def test_cleanup_order_page_context_browser_playwright_imap(self, mock_navigate):
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
    @patch("main.navigate_to_dashboard")
    def test_keyboard_interrupt_returns_zero(self, mock_navigate):
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
    @patch("main.navigate_to_dashboard")
    def test_recovery_exhausted_returns_one(self, mock_navigate):
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



class TestStartupDashboardNavigationRecovery:
    @patch("main.time.sleep")
    @patch("main.navigate_to_dashboard")
    def test_startup_navigation_recovers_after_retry(self, mock_navigate, mock_sleep):
        from dashboard_monitor import RecoveryError

        mock_navigate.side_effect = [RecoveryError("Temporary navigation error"), None]

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
                        assert mock_navigate.call_count == 2
                        mock_sleep.assert_called_once_with(5)
                        mock_monitor_class.assert_called_once()

    @patch("main.time.sleep")
    @patch("main.navigate_to_dashboard")
    def test_startup_navigation_exhausts_retries_returns_error(self, mock_navigate, mock_sleep):
        from dashboard_monitor import RecoveryError

        mock_navigate.side_effect = RecoveryError("Persistent navigation error")

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
                        import main
                        result = main.main(env=TEST_ENV)

                        assert result == 1
                        assert mock_navigate.call_count == 3
                        assert mock_sleep.call_count == 2
                        mock_monitor_class.assert_not_called()

    @patch("main.time.sleep")
    @patch("main.navigate_to_dashboard")
    def test_authentication_error_not_retried(self, mock_navigate, mock_sleep):
        from cmp_auth import AuthenticationError

        mock_navigate.side_effect = AuthenticationError("Auth error in nav")

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
                        import main
                        result = main.main(env=TEST_ENV)

                        assert result == 1
                        assert mock_navigate.call_count == 1
                        mock_sleep.assert_not_called()
                        mock_monitor_class.assert_not_called()


class TestLoggingSetup:
    def test_timestamped_default_log_filename(self, tmp_path, monkeypatch):
        import logging
        import re

        from main import setup_logging

        monkeypatch.chdir(tmp_path)
        setup_logging("INFO", log_file=None)

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) >= 1

        created_files = list(tmp_path.glob("logs/monitor_*.log")) or list(tmp_path.glob("monitor_*.log"))
        assert len(created_files) == 1
        assert re.match(r"^monitor_\d{8}_\d{6}\.log$", created_files[0].name)

        for h in list(root.handlers):
            if getattr(h, "_cmp_owned", False):
                root.removeHandler(h)
                h.close()

    def test_explicit_log_path(self, tmp_path):
        import logging

        from main import setup_logging

        explicit_path = tmp_path / "custom_logs" / "test_run.log"
        setup_logging("DEBUG", log_file=explicit_path)

        assert explicit_path.parent.exists()
        assert explicit_path.exists()

        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_cmp_owned", False):
                root.removeHandler(h)
                h.close()

    def test_formatter_includes_readable_date_and_time(self, tmp_path):
        import logging
        import re

        from main import setup_logging

        explicit_path = tmp_path / "timestamp.log"
        setup_logging("INFO", log_file=explicit_path)
        logging.getLogger("test").info("timestamp check")

        output = explicit_path.read_text(encoding="utf-8")
        assert re.search(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] INFO:test: timestamp check$", output.strip())

        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_cmp_owned", False):
                root.removeHandler(h)
                h.close()

    def test_main_handled_failure_writes_single_start_and_end_without_exception_text(self, tmp_path):
        import logging

        import main

        log_path = tmp_path / "handled-failure.log"
        real_setup_logging = main.setup_logging

        def configure_logging(level):
            real_setup_logging(level, log_file=log_path)

        with patch("main.setup_logging", side_effect=configure_logging):
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
                    mock_imap_class.return_value = MagicMock()
                    with patch(
                        "main.authenticate_cmp",
                        side_effect=main.AuthenticationError("sensitive_token_abc123"),
                    ):
                        assert main.main(env=TEST_ENV) == 1

        output = log_path.read_text(encoding="utf-8")
        assert output.count("==================== START CMP Dashboard Monitor ====================") == 1
        assert output.count("==================== END CMP Dashboard Monitor ====================") == 1
        assert "sensitive_token_abc123" not in output

        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_cmp_owned", False):
                root.removeHandler(handler)
                handler.close()

    def test_main_keyboard_interrupt_writes_single_start_and_end(self, tmp_path):
        import logging

        import main

        log_path = tmp_path / "keyboard-interrupt.log"
        real_setup_logging = main.setup_logging

        def configure_logging(level):
            real_setup_logging(level, log_file=log_path)

        with patch("main.setup_logging", side_effect=configure_logging):
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
                    mock_imap_class.return_value = MagicMock()
                    with patch("main.authenticate_cmp"):
                        with patch("main.navigate_to_dashboard"):
                            with patch("main.ContinuousMonitor") as mock_monitor_class:
                                mock_monitor_class.return_value.monitor_once.side_effect = KeyboardInterrupt()
                                assert main.main(env=TEST_ENV) == 0

        output = log_path.read_text(encoding="utf-8")
        assert output.count("==================== START CMP Dashboard Monitor ====================") == 1
        assert output.count("==================== END CMP Dashboard Monitor ====================") == 1

        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_cmp_owned", False):
                root.removeHandler(handler)
                handler.close()

    def test_repeated_setup_without_duplicate_owned_handlers_or_closing_unrelated(self, tmp_path):
        import logging

        from main import setup_logging

        root = logging.getLogger()

        unrelated_handler = logging.Handler()
        unrelated_closed = False

        def mark_closed():
            nonlocal unrelated_closed
            unrelated_closed = True

        unrelated_handler.close = mark_closed
        root.addHandler(unrelated_handler)

        try:
            log1 = tmp_path / "run1.log"
            log2 = tmp_path / "run2.log"

            setup_logging("INFO", log_file=log1)
            owned_after_first = [h for h in root.handlers if getattr(h, "_cmp_owned", False)]
            assert len(owned_after_first) == 2

            setup_logging("DEBUG", log_file=log2)
            owned_after_second = [h for h in root.handlers if getattr(h, "_cmp_owned", False)]
            assert len(owned_after_second) == 2

            assert unrelated_handler in root.handlers
            assert not unrelated_closed
        finally:
            if unrelated_handler in root.handlers:
                root.removeHandler(unrelated_handler)
            for h in list(root.handlers):
                if getattr(h, "_cmp_owned", False):
                    root.removeHandler(h)
                    h.close()


class TestExceptionTracebackAndCleanupLogging:
    @patch("main.navigate_to_dashboard")
    def test_monitoring_recovery_exhausted_uses_safe_fixed_context(self, mock_navigate):
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
                        mock_monitor.monitor_once.side_effect = RecoveryExhaustedError("sensitive_token_abc123")
                        mock_monitor_class.return_value = mock_monitor

                        with patch("main.log") as mock_log:
                            import main

                            result = main.main(env=TEST_ENV)

                            assert result == 1
                            mock_log.error.assert_called_with(
                                "Recovery exhausted during monitoring (%s)",
                                "RecoveryExhaustedError",
                            )

    @patch("main.navigate_to_dashboard")
    def test_cleanup_failure_logged_as_warning(self, mock_navigate):
        with patch("main.sync_playwright") as mock_sync:
            mock_playwright = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_page.close.side_effect = RuntimeError("Page close error")

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

                        with patch("main.log") as mock_log:
                            import main

                            result = main.main(env=TEST_ENV)

                            assert result == 0
                            mock_log.warning.assert_called()
                            warning_call_args = [call[0][0] for call in mock_log.warning.call_args_list]
                            assert any("Cleanup failure" in msg or "Failed to close page" in msg for msg in warning_call_args)

    @patch("main.navigate_to_dashboard")
    def test_unexpected_exception_logs_message_and_traceback(self, mock_navigate):
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

                exc = RuntimeError("Unexpected boom")
                with patch("main.authenticate_cmp", side_effect=exc):
                    with patch("main.log") as mock_log:
                        import main

                        result = main.main(env=TEST_ENV)

                        assert result == 1
                        mock_log.error.assert_called_with(
                            "Monitor failed (%s%s): %s",
                            "RuntimeError",
                            "",
                            exc,
                            exc_info=True,
                        )



class TestMainVpnLifecycle:
    @patch("main.navigate_to_dashboard")
    def test_main_vpn_lifecycle_ordering(self, mock_navigate):
        from vpn import ConnectivityController

        events = []
        vpn_mock = MagicMock(spec=ConnectivityController)
        vpn_mock.prepare_reauthentication.side_effect = lambda: events.append("vpn_prepare_reauthentication")
        vpn_mock.finish_authentication.side_effect = lambda: events.append("vpn_finish_authentication")

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
                with patch("main.authenticate_cmp") as mock_auth:
                    def _fake_auth(*args, **kwargs):
                        events.append("authenticate_cmp")
                        hook = kwargs.get("on_otp_submitted")
                        if hook:
                            hook()
                        return True
                    mock_auth.side_effect = _fake_auth
                    mock_navigate.side_effect = lambda *args, **kwargs: events.append("navigate_to_dashboard") or True
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        mock_monitor = MagicMock()
                        mock_monitor.monitor_once.side_effect = KeyboardInterrupt()
                        mock_monitor_class.return_value = mock_monitor

                        import main
                        result = main.main(env=TEST_ENV, connectivity_factory=lambda *args: vpn_mock)

                        assert result == 0
                        assert events == [
                            "authenticate_cmp",
                            "vpn_finish_authentication",
                            "navigate_to_dashboard",
                        ]
                        mock_monitor_class.assert_called_once()

    def test_main_vpn_does_not_connect_on_auth_failure(self):
        from cmp_auth import AuthenticationError
        from vpn import ConnectivityController

        vpn_mock = MagicMock(spec=ConnectivityController)

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
                    with patch("main.ContinuousMonitor") as mock_monitor_class:
                        import main
                        result = main.main(env=TEST_ENV, connectivity_factory=lambda *args: vpn_mock)

                        assert result == 1
                        vpn_mock.prepare_reauthentication.assert_not_called()
                        vpn_mock.finish_authentication.assert_not_called()
                        mock_monitor_class.assert_not_called()
