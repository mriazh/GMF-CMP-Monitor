"""CMP Dashboard Monitor - Entry point.

Monitors the Telkomsel CMP dashboard for GMF using Firefox Playwright.
Does not launch browser during import.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Mapping

from playwright.sync_api import sync_playwright

from config import load_settings, ConfigError
from imap_client import ImapClient, SystemClock
from cmp_auth import authenticate_cmp, AuthenticationError
from dashboard_monitor import ContinuousMonitor, DashboardState, RecoveryError, RecoveryExhaustedError, navigate_to_dashboard

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
    # 1. Load configuration
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
        # 2. Create IMAP client (object only; connect right before authentication)
        clock = SystemClock()
        imap_client = ImapClient(settings, clock)

        # 3. Launch Firefox browser
        playwright = sync_playwright().start()
        browser = playwright.firefox.launch(headless=settings.headless)
        context = browser.new_context()
        page = context.new_page()
        log.info("Firefox launched (headless=%s)", settings.headless)

        # 4. Connect IMAP right before authentication to minimize idle time
        log.info("Connecting to IMAP...")
        imap_client.connect()
        log.info("IMAP connected")

        # 5. Authenticate
        authenticate_cmp(settings=settings, otp_provider=imap_client, page=page, clock=clock)
        log.info("Authentication successful")

        # 6. Navigate to dashboard via SPA menu (fallback: direct goto)
        navigate_to_dashboard(page, settings)
        log.info("Navigated to dashboard")

        # 6. Monitor
        monitor = ContinuousMonitor(settings=settings, page=page, otp_provider=imap_client, clock=clock)

        while True:
            try:
                need_relogin = monitor.monitor_once()

                if need_relogin:
                    log.info("Re-authentication performed")
                    # monitor_once already handles relogin and navigation to dashboard
            except RecoveryExhaustedError as exc:
                log.error("Recovery exhausted: %s", exc)
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
    except AuthenticationError as exc:
        log.error("Authentication failed")
        return 1
    except RecoveryExhaustedError:
        log.error("Recovery exhausted")
        return 1
    except RecoveryError:
        log.error("Dashboard recovery failed")
        return 1
    except Exception:
        log.error("Monitor failed")
        return 1
    finally:
        # Cleanup all resources in reverse order of creation
        log.info("Cleaning up resources...")
        
        # Close page
        if page is not None:
            try:
                page.close()
            except (Exception, KeyboardInterrupt):
                pass

        # Close context
        if context is not None:
            try:
                context.close()
            except (Exception, KeyboardInterrupt):
                pass

        # Close browser
        if browser is not None:
            try:
                browser.close()
            except (Exception, KeyboardInterrupt):
                pass

        # Stop playwright
        if playwright is not None:
            try:
                playwright.stop()
            except (Exception, KeyboardInterrupt):
                pass
        
        # Disconnect IMAP
        if imap_client is not None:
            try:
                imap_client.disconnect()
            except (Exception, KeyboardInterrupt):
                pass
        
    return 0


if __name__ == "__main__":
    sys.exit(main())