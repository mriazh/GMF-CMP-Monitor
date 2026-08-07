"""Opt-in live IMAP-first workflow tests.

These tests are excluded by the default pytest configuration. They are intended
for an operator-approved run on the Windows workstation with the configured
Check Point, WARP, IMAP, and CMP clients available.

The workflow test owns only VPN connections that it starts. It captures the
initial Check Point/WARP and unrelated-VPN state, closes IMAP before Check Point
teardown, validates WARP, performs one bounded dashboard check, and verifies the
initial state afterward. Secrets, OTPs, public IP values, trace bodies, and raw
subprocess output are never logged or persisted.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from cmp_auth import authenticate_cmp
from config import PROJECT_ROOT, ConfigError, load_settings
from dashboard_monitor import DashboardState, classify_state, navigate_to_dashboard
from imap_client import ImapClient, SystemClock
from network_diag import get_public_ip, run_network_diagnostic
from network_probe import ImapReachabilityProbe
from vpn import (
    CheckPointClient,
    ConnectivityController,
    VpnError,
    WarpClient,
    WarpError,
)


def _load_live_settings() -> object:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env file not present in project root")
    try:
        return load_settings(env_file=env_path)
    except ConfigError:
        pytest.skip("live environment configuration is incomplete")


def _unrelated_vpn_providers(report: object) -> tuple[str, ...]:
    return tuple(
        provider
        for provider in report.vpn_providers
        if provider not in {"Check Point", "Cloudflare WARP"}
    )


@pytest.mark.live
def test_live_network_position_and_public_ip_probe():
    """Run only read-only office/CMP reachability and public-IP diagnostics."""
    settings = _load_live_settings()
    checkpoint = CheckPointClient(settings)
    report = run_network_diagnostic(settings=settings, checkpoint_client=checkpoint)
    public_ip = get_public_ip()

    assert report.local_ip
    assert report.position.value in {
        "CORPORATE_LAN",
        "VPN_CONNECTED",
        "PUBLIC_INTERNET",
        "OFFLINE",
        "UNKNOWN",
        "OFFICE_NETWORK_PARTIAL_CMP",
    }
    assert public_ip is None or isinstance(public_ip, str)


@pytest.mark.live
def test_live_imap_first_cmp_dashboard_roundtrip():
    """Exercise the approved IMAP-first, owned-VPN cleanup workflow once."""
    settings = _load_live_settings()
    checkpoint = CheckPointClient(settings)
    warp = WarpClient(settings)
    probe = ImapReachabilityProbe(
        settings.office_network_probe_host,
        settings.office_network_probe_port,
        settings.office_network_probe_timeout_seconds,
    )
    controller = ConnectivityController(settings, probe, checkpoint, warp)

    try:
        checkpoint_before = checkpoint.status().connected
        warp_before = warp.status().connected
    except VpnError:
        pytest.skip("configured VPN client status is unavailable")

    if checkpoint_before or warp_before:
        pytest.skip("pre-existing Check Point/WARP state must be disconnected for this roundtrip test")

    before_report = run_network_diagnostic(settings=settings, checkpoint_client=checkpoint)
    unrelated_before = _unrelated_vpn_providers(before_report)
    imap_client = ImapClient(settings, SystemClock())
    clock = SystemClock()
    browser = None
    context = None
    page = None

    def on_otp_submitted() -> None:
        imap_client.disconnect()
        controller.finish_authentication()

    try:
        controller.ensure_imap_reachable()
        imap_client.connect()

        with sync_playwright() as playwright:
            launch_options: dict[str, bool | str] = {"headless": True}
            if settings.firefox_executable_path is not None:
                launch_options["executable_path"] = str(settings.firefox_executable_path)
            browser = playwright.firefox.launch(**launch_options)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            assert authenticate_cmp(
                settings=settings,
                otp_provider=imap_client,
                page=page,
                clock=clock,
            ) is True

            imap_client.disconnect()
            controller.finish_authentication()

            navigate_to_dashboard(page, settings, clock)
            assert classify_state(page) == DashboardState.DASHBOARD
    finally:
        try:
            imap_client.disconnect()
        except (OSError, RuntimeError):
            pass
        controller.cleanup()
        if context is not None:
            try:
                context.close()
            except (OSError, PlaywrightError, RuntimeError):
                pass
        if browser is not None:
            try:
                browser.close()
            except (OSError, PlaywrightError, RuntimeError):
                pass

    try:
        checkpoint_after = checkpoint.status().connected
        warp_after = warp.status().connected
    except (VpnError, WarpError):
        pytest.fail("could not verify VPN state restoration")

    after_report = run_network_diagnostic(settings=settings, checkpoint_client=checkpoint)
    unrelated_after = _unrelated_vpn_providers(after_report)
    assert checkpoint_after is checkpoint_before
    assert warp_after is warp_before
    assert unrelated_after == unrelated_before
