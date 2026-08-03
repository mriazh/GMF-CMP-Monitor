"""CMP Dashboard Monitor - Entry point.

Monitors the Telkomsel CMP dashboard for GMF using Firefox Playwright.
Does not launch browser during import.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Mapping
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


def setup_logging(level: str, log_file: str = "monitor.log") -> None:
    """Configure logging with redacted sensitive content."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s:%(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.basicConfig(level=level, handlers=[handler, logging.FileHandler(log_file, encoding="utf-8")])


def main(env_file: str | Path | None = None, env: Mapping[str, str] | None = None) -> int:
    """Main entry point. Does not launch browser during module import."""
    try:
        settings = load_settings(env=env, env_file=env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(settings.log_level)
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
            except RecoveryError:
                log.warning(
                    "Startup dashboard navigation attempt %d/%d failed",
                    attempt,
                    settings.recovery_retry_limit,
                )
                if attempt >= settings.recovery_retry_limit:
                    log.error("Startup dashboard navigation recovery exhausted")
                    return 1
                time.sleep(settings.recovery_backoff_seconds)

        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=imap_client, clock=clock)

        while True:
            try:
                need_relogin = monitor.monitor_once()

                if need_relogin:
                    log.info("Re-authentication performed")
            except RecoveryExhaustedError:
                log.error("Recovery exhausted during monitoring")
                return 1
            except AuthenticationError:
                log.error("Authentication error during monitoring")
                return 1
            except RecoveryError:
                log.error("Dashboard recovery failed during monitoring")
                return 1

    except KeyboardInterrupt:
        log.info("Shutting down via keyboard interrupt")
        return 0
    except AuthenticationError:
        log.error("Authentication failed")
        return 1
    except RecoveryExhaustedError:
        log.error("Recovery exhausted")
        return 1
    except RecoveryError:
        log.error("Dashboard recovery failed")
        return 1
    except Exception:  # noqa: BLE001
        log.error("Monitor failed")
        return 1
    finally:
        log.info("Cleaning up resources...")

        if page is not None:
            try:
                page.close()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001, S110
                pass

        if context is not None:
            try:
                context.close()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001, S110
                pass

        if browser is not None:
            try:
                browser.close()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001, S110
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001, S110
                pass

        if imap_client is not None:
            try:
                imap_client.disconnect()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001, S110
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
