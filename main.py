"""CMP Dashboard Monitor - Entry point.

Monitors the Telkomsel CMP dashboard for GMF using Firefox Playwright.
Does not launch browser during import.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
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
from network_probe import ImapReachabilityProbe
from vpn import CheckPointClient, ConnectivityController, WarpClient

log = logging.getLogger(__name__)


def _safe_disconnect(resource: object | None, label: str) -> None:
    if resource is None:
        return
    try:
        disconnect = getattr(resource, "disconnect", None)
        if disconnect is not None:
            disconnect()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        log.warning("Cleanup failure disconnecting %s (%s)", label, type(exc).__name__)


class _SafeFileHandler(logging.FileHandler):
    """FileHandler that closes file stream when idle to prevent file lock on Windows."""

    def __init__(self, filename: str | Path, encoding: str = "utf-8") -> None:
        super().__init__(filename, encoding=encoding)
        self.close()

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.close()


def _resolve_base_dir() -> Path:
    """Resolve the application base directory for both development and frozen runs."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _configure_bundled_browser(base_dir: Path) -> None:
    """Point Playwright at the bundled browser directory when one ships alongside the app."""
    bundled_browsers = base_dir / "browsers"
    if bundled_browsers.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)


BASE_DIR = _resolve_base_dir()
_configure_bundled_browser(BASE_DIR)


def setup_logging(
    level: str | int,
    log_file: str | Path | None = None,
) -> None:
    """Configure logging with redacted sensitive content and timestamped default log path."""
    if log_file is None:
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        if getattr(sys, "frozen", False):
            log_path = BASE_DIR / "logs" / f"monitor_{timestamp}.log"
        else:
            log_path = Path("logs") / f"monitor_{timestamp}.log"
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


def main(
    env_file: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    imap_factory: Callable[..., object] | None = None,
    probe_factory: Callable[..., object] | None = None,
    checkpoint_factory: Callable[..., object] | None = None,
    warp_factory: Callable[..., object] | None = None,
    connectivity_factory: Callable[..., object] | None = None,
) -> int:
    """Main entry point. Does not launch browser during module import."""
    if env_file is None and env is None:
        default_env = BASE_DIR / ".env"
        if default_env.exists():
            env_file = default_env
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
    connectivity = None
    playwright = None
    browser = None
    context = None
    page = None

    try:
        clock = SystemClock()
        imap_client = (imap_factory or ImapClient)(settings, clock)
        try:
            probe = (probe_factory or ImapReachabilityProbe)(
                settings.office_network_probe_host,
                settings.office_network_probe_port,
                settings.office_network_probe_timeout_seconds,
                verify_tls=settings.imap_verify_tls,
            )
        except TypeError:
            probe = (probe_factory or ImapReachabilityProbe)(
                settings.office_network_probe_host,
                settings.office_network_probe_port,
                settings.office_network_probe_timeout_seconds,
            )
        checkpoint = (checkpoint_factory or CheckPointClient)(settings)
        warp = (warp_factory or WarpClient)(settings)
        connectivity = (connectivity_factory or ConnectivityController)(
            settings, probe, checkpoint, warp
        )
        # Offline unit tests using mock settings with fake hostnames / without actual trac/warp
        # configuration skip real subprocess probe/connect. Real runs enforce IMAP reachability.
        if (
            connectivity_factory is not None
            or settings.checkpoint_trac_path is not None
            or settings.warp_cli_path is not None
        ):
            connectivity.ensure_imap_reachable()
            if settings.warp_mode == "proxy":
                connectivity.prepare_warp()
                log.info("WARP SOCKS5 proxy ready on port %d", settings.warp_proxy_port)

        log.info("Initializing browser driver...")
        playwright = sync_playwright().start()
        log.info("Launching bundled Firefox browser (headless=%s)...", settings.headless)

        launch_args: list[str] = []
        if settings.viewport_auto:
            launch_args.append("--start-maximized")

        launch_kwargs: dict[str, object] = {"headless": settings.headless}
        if launch_args:
            launch_kwargs["args"] = launch_args
        if settings.firefox_executable_path:
            launch_kwargs["executable_path"] = str(settings.firefox_executable_path.resolve())
        if settings.warp_mode == "proxy":
            launch_kwargs["proxy"] = {"server": f"socks5://127.0.0.1:{settings.warp_proxy_port}"}
            log.info("Firefox proxy configured: socks5://127.0.0.1:%d (isolated browser routing)", settings.warp_proxy_port)

        browser = playwright.firefox.launch(**launch_kwargs)

        if settings.viewport_auto:
            context = browser.new_context(no_viewport=True)
            log.info("Firefox launched with auto-responsive viewport (adapts to display)")
        else:
            context = browser.new_context(viewport={"width": settings.viewport_width, "height": settings.viewport_height})
            log.info("Firefox launched (viewport=%dx%d)", settings.viewport_width, settings.viewport_height)

        if settings.page_zoom_percent != 100:
            zoom_pct = settings.page_zoom_percent
            context.add_init_script(f"""
                (() => {{
                    const applyRouteAwareZoom = () => {{
                        let style = document.getElementById('cmp-custom-zoom');
                        const isDashboard = window.location.hash.includes('!dashboard');
                        const targetZoom = isDashboard ? '{zoom_pct}%' : '100%';
                        if (!style) {{
                            style = document.createElement('style');
                            style.id = 'cmp-custom-zoom';
                            if (document.head) {{
                                document.head.appendChild(style);
                            }} else {{
                                document.addEventListener('DOMContentLoaded', () => {{
                                    if (document.head && !document.getElementById('cmp-custom-zoom')) {{
                                        document.head.appendChild(style);
                                    }}
                                }});
                            }}
                        }}
                        if (style && style.textContent !== `body, html {{ zoom: ${{targetZoom}} !important; }}`) {{
                            style.textContent = `body, html {{ zoom: ${{targetZoom}} !important; }}`;
                        }}
                    }};
                    applyRouteAwareZoom();
                    window.addEventListener('hashchange', applyRouteAwareZoom);
                    window.addEventListener('popstate', applyRouteAwareZoom);
                    document.addEventListener('DOMContentLoaded', applyRouteAwareZoom);
                    window.addEventListener('load', applyRouteAwareZoom);
                    setInterval(applyRouteAwareZoom, 500);
                }})();
            """)
            log.info("Dashboard-scoped zoom configured: %d%% (CAS login and products remain 100%%)", zoom_pct)

        page = context.new_page()

        log.info("Connecting to IMAP...")
        imap_client.connect()
        log.info("IMAP connected")

        warp_activated = False

        def _on_otp_submitted() -> None:
            nonlocal warp_activated
            if connectivity_factory or settings.checkpoint_trac_path is not None or settings.warp_cli_path is not None:
                _safe_disconnect(imap_client, "IMAP client")
                connectivity.finish_authentication()
                warp_activated = True
                log.info("WARP full-tunnel validation successful")

        authenticate_cmp(
            settings=settings,
            otp_provider=imap_client,
            page=page,
            clock=clock,
            on_otp_submitted=_on_otp_submitted,
        )
        log.info("Authentication successful")

        if not warp_activated and (connectivity_factory or settings.checkpoint_trac_path is not None or settings.warp_cli_path is not None):
            _safe_disconnect(imap_client, "IMAP client")
            connectivity.finish_authentication()
            log.info("WARP full-tunnel validation successful")

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

        def restore_auth_connectivity() -> None:
            _safe_disconnect(imap_client, "IMAP client")
            if connectivity_factory or settings.checkpoint_trac_path is not None or settings.warp_cli_path is not None:
                connectivity.prepare_reauthentication()
            imap_client.connect()

        def finish_reauthentication() -> None:
            _safe_disconnect(imap_client, "IMAP client")
            if connectivity_factory or settings.checkpoint_trac_path is not None or settings.warp_cli_path is not None:
                connectivity.finish_authentication()

        monitor = ContinuousMonitor(
            settings=settings,
            page=page,
            otp_provider=imap_client,
            clock=clock,
            before_relogin=restore_auth_connectivity,
            after_relogin=finish_reauthentication,
        )

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
        cause = getattr(exc, "__cause__", None)
        cause_str = f" caused by {type(cause).__name__}" if cause else ""
        log.error("Monitor failed (%s%s)", type(exc).__name__, cause_str)
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

        if connectivity is not None:
            try:
                connectivity.cleanup()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                log.warning("Cleanup failure closing connectivity (%s)", type(exc).__name__)

        log.info("==================== END CMP Dashboard Monitor ====================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
