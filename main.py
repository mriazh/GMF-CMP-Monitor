"""CMP Dashboard Monitor - Entry point.

Monitors the Telkomsel CMP dashboard for GMF using Firefox Playwright.
Does not launch browser during import.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from cmp_auth import AuthenticationError, authenticate_cmp
from config import ConfigError, load_settings
from dashboard_monitor import (
    ContinuousMonitor,
    RecoveryError,
    RecoveryExhaustedError,
    navigate_to_dashboard,
)
from imap_client import ImapClient, SystemClock

log = logging.getLogger(__name__)


class _SafeFileHandler(logging.FileHandler):
    """FileHandler that closes file stream when idle to prevent file lock on Windows."""

    def __init__(self, filename: str | Path, encoding: str = "utf-8") -> None:
        super().__init__(filename, encoding=encoding)
        self.close()

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.close()


def setup_logging(
    level: str | int,
    log_file: str | Path | None = None,
) -> None:
    """Configure logging with redacted sensitive content and timestamped default log path."""
    if log_file is None:
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        log_path = Path(f"monitor_{timestamp}.log")
    else:
        log_path = Path(log_file)

    if (
        log_path.parent
        and log_path.parent != Path("")
        and log_path.parent != Path(".")
        and not log_path.parent.exists()
    ):
        log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()

    for handler in list(root_logger.handlers):
        if getattr(handler, "_cmp_owned", False):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001, S110
                pass

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s:%(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler._cmp_owned = True

    file_handler = _SafeFileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._cmp_owned = True

    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(file_handler)

    if isinstance(level, str):
        root_logger.setLevel(level.upper())
    else:
        root_logger.setLevel(level)


def main(env_file: str | Path | None = None, env: Mapping[str, str] | None = None) -> int:
    """Main entry point. Does not launch browser during module import."""
    try:
        settings = load_settings(env=env, env_file=env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(settings.log_level)
    log.info("==================== START CMP Dashboard Monitor ====================")
    log.info("Starting CMP Dashboard Monitor")
    log.info("Refresh interval: %.0fs", settings.refresh_interval_seconds)

    imap_client = None
    playwright = None
    browser = None
    context = None
    page = None

    try:
        clock = SystemClock()
        imap_client = ImapClient(settings, clock)

        playwright = sync_playwright().start()
        launch_kwargs = {"headless": settings.headless}
        if settings.firefox_executable_path:
            launch_kwargs["executable_path"] = str(settings.firefox_executable_path.resolve())
        browser = playwright.firefox.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        log.info("Firefox launched (headless=%s, viewport=1920x1080)", settings.headless)

        log.info("Connecting to IMAP...")
        imap_client.connect()
        log.info("IMAP connected")

        authenticate_cmp(settings=settings, otp_provider=imap_client, page=page, clock=clock)
        log.info("Authentication successful")

        for attempt in range(1, settings.recovery_retry_limit + 1):
            try:
                navigate_to_dashboard(page, settings)
                log.info("Navigated to dashboard")
                break
            except RecoveryError as exc:
                log.warning(
                    "Startup dashboard navigation attempt %d/%d failed",
                    attempt,
                    settings.recovery_retry_limit,
                )
                if attempt >= settings.recovery_retry_limit:
                    log.error(
                        "Startup dashboard navigation recovery exhausted (%s)",
                        type(exc).__name__,
                    )
                    return 1
                time.sleep(settings.recovery_backoff_seconds)

        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=imap_client, clock=clock)

        while True:
            try:
                need_relogin = monitor.monitor_once()

                if need_relogin:
                    log.info("Re-authentication performed")
            except RecoveryExhaustedError as exc:
                log.error("Recovery exhausted during monitoring (%s)", type(exc).__name__)
                return 1
            except AuthenticationError as exc:
                log.error("Authentication error during monitoring (%s)", type(exc).__name__)
                return 1
            except RecoveryError as exc:
                log.error("Dashboard recovery failed during monitoring (%s)", type(exc).__name__)
                return 1

    except KeyboardInterrupt:
        log.info("Shutting down via keyboard interrupt")
        return 0
    except AuthenticationError as exc:
        log.error("Authentication failed (%s)", type(exc).__name__)
        return 1
    except RecoveryExhaustedError as exc:
        log.error("Recovery exhausted (%s)", type(exc).__name__)
        return 1
    except RecoveryError as exc:
        log.error("Dashboard recovery failed (%s)", type(exc).__name__)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("Monitor failed (%s)", type(exc).__name__)
        return 1
    finally:
        log.info("Cleaning up resources...")

        if page is not None:
            try:
                page.close()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure closing page (%s)", type(exc).__name__)

        if context is not None:
            try:
                context.close()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure closing context (%s)", type(exc).__name__)

        if browser is not None:
            try:
                browser.close()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure closing browser (%s)", type(exc).__name__)

        if playwright is not None:
            try:
                playwright.stop()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure stopping playwright (%s)", type(exc).__name__)

        if imap_client is not None:
            try:
                imap_client.disconnect()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure disconnecting IMAP client (%s)", type(exc).__name__)

        log.info("==================== END CMP Dashboard Monitor ====================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
