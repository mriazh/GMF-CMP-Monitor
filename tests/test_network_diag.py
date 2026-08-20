"""Offline tests for network_diag module.

All tests are strictly offline and mock all socket, subprocess, and network calls.
"""

from __future__ import annotations

import socket
import subprocess
from unittest.mock import MagicMock, patch

from config import Settings, load_settings
from network_diag import (
    NetworkPosition,
    check_dns,
    check_tcp_port,
    detect_active_vpn_interfaces,
    get_local_ip,
    run_network_diagnostic,
    sanitize_diagnostic_output,
)


def _make_dummy_settings() -> Settings:
    return load_settings(
        env={
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpassword",
            "IMAP_USERNAME": "testimapuser",
            "IMAP_PASSWORD": "testimappassword",
        },
        env_file=None,
    )


class TestSanitizeDiagnosticOutput:
    """Tests for diagnostic output sanitization."""

    def test_sanitize_empty(self):
        assert sanitize_diagnostic_output("") == ""

    def test_sanitize_url_credentials(self):
        text = "Connecting to https://user:secretpass123@ep.iotcc.telkomsel.com"
        sanitized = sanitize_diagnostic_output(text)
        assert "secretpass123" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_sanitize_tokens_and_passwords(self):
        text = "password=SuperSecret123 token=abc123456 otp=987654"
        sanitized = sanitize_diagnostic_output(text)
        assert "SuperSecret123" not in sanitized
        assert "abc123456" not in sanitized
        assert "987654" not in sanitized

    def test_sanitize_standalone_otp_digits(self):
        text = "Received OTP code 123456 for login"
        sanitized = sanitize_diagnostic_output(text)
        assert "123456" not in sanitized
        assert "***REDACTED_OTP***" in sanitized


class TestCheckDns:
    """Tests for safe DNS check."""

    def test_check_dns_empty_host(self):
        assert check_dns("") is False

    @patch("socket.getaddrinfo")
    def test_check_dns_success(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))
        ]
        assert check_dns("ep.iotcc.telkomsel.com") is True
        mock_getaddrinfo.assert_called_once_with(
            "ep.iotcc.telkomsel.com", None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known"))
    def test_check_dns_failure(self, _mock_getaddrinfo):
        assert check_dns("invalid.host.nonexistent") is False


class TestCheckTcpPort:
    """Tests for safe TCP port probe."""

    def test_check_tcp_invalid_args(self):
        assert check_tcp_port("", 443) is False
        assert check_tcp_port("localhost", 0) is False
        assert check_tcp_port("localhost", -1) is False

    @patch("socket.socket")
    def test_check_tcp_port_success(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        result = check_tcp_port("ep.iotcc.telkomsel.com", 443, timeout=2.0)
        assert result is True
        mock_sock.settimeout.assert_called_once_with(2.0)
        mock_sock.connect.assert_called_once_with(("ep.iotcc.telkomsel.com", 443))
        # Ensure run-owned socket is closed
        mock_sock.close.assert_called_once()

    @patch("socket.socket")
    def test_check_tcp_port_failure(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = TimeoutError("Connection timed out")
        mock_socket_cls.return_value = mock_sock

        result = check_tcp_port("ep.iotcc.telkomsel.com", 443, timeout=1.0)
        assert result is False
        mock_sock.close.assert_called_once()


class TestGetLocalIP:
    """Tests for safe local IP discovery."""

    @patch("socket.socket")
    def test_get_local_ip_via_udp_socket(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("10.10.20.30", 54321)
        mock_socket_cls.return_value = mock_sock

        ip = get_local_ip()
        assert ip == "10.10.20.30"
        mock_sock.close.assert_called_once()

    @patch("socket.socket", side_effect=OSError("Network unreachable"))
    @patch("socket.gethostname", return_value="myhost")
    @patch("socket.gethostbyname", return_value="172.16.0.5")
    def test_get_local_ip_fallback_to_hostname(self, _mock_byname, _mock_hostname, _mock_sock):
        ip = get_local_ip()
        assert ip == "172.16.0.5"

    @patch("socket.socket", side_effect=OSError("Network unreachable"))
    @patch("socket.gethostname", side_effect=OSError("Cannot get hostname"))
    def test_get_local_ip_fallback_to_localhost(self, _mock_hostname, _mock_sock):
        ip = get_local_ip()
        assert ip == "127.0.0.1"


class TestDetectActiveVpnInterfaces:
    """Tests for read-only VPN interface detection."""

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    def test_detect_checkpoint_vpn_windows(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="""
Admin State    State          Type             Interface Name
-------------------------------------------------------------------------
Enabled        Connected      Dedicated        Check Point Virtual Network Adapter
Enabled        Connected      Dedicated        Wi-Fi
"""
        )
        vpns = detect_active_vpn_interfaces()
        assert "Check Point" in vpns

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    def test_detect_warp_vpn_windows(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="""
Admin State    State          Type             Interface Name
-------------------------------------------------------------------------
Enabled        Connected      Dedicated        CloudflareWARP
Enabled        Connected      Dedicated        Ethernet
"""
        )
        vpns = detect_active_vpn_interfaces()
        assert "Cloudflare WARP" in vpns

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    def test_detect_no_vpn_windows(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="""
Admin State    State          Type             Interface Name
-------------------------------------------------------------------------
Enabled        Connected      Dedicated        Ethernet
Enabled        Disconnected   Dedicated        Wi-Fi
"""
        )
        vpns = detect_active_vpn_interfaces()
        assert vpns == []

    @patch("sys.platform", "win32")
    @patch("subprocess.run", side_effect=subprocess.SubprocessError("Timeout"))
    def test_detect_vpn_subprocess_error_handled(self, _mock_run):
        vpns = detect_active_vpn_interfaces()
        assert vpns == []


class TestRunNetworkDiagnostic:
    """Tests for run_network_diagnostic."""

    @patch("network_diag.get_local_ip", return_value="10.20.30.40")
    @patch("network_diag.detect_active_vpn_interfaces", return_value=["Check Point"])
    @patch("network_diag.check_dns", return_value=True)
    @patch("network_diag.check_tcp_port", return_value=True)
    def test_run_network_diagnostic_vpn_connected(self, mock_tcp, mock_dns, _mock_vpn, _mock_ip):
        report = run_network_diagnostic()

        assert report.local_ip == "10.20.30.40"
        assert report.vpn_detected is True
        assert "Check Point" in report.vpn_providers
        assert report.position == NetworkPosition.VPN_CONNECTED
        assert report.ready_for_live_test is True
        assert "Live Test Ready: True" in report.summary

    @patch("network_diag.get_local_ip", return_value="10.0.0.15")
    @patch("network_diag.detect_active_vpn_interfaces", return_value=[])
    @patch("network_diag.check_dns", return_value=True)
    @patch("network_diag.check_tcp_port", return_value=True)
    def test_run_network_diagnostic_corporate_lan(self, mock_tcp, mock_dns, _mock_vpn, _mock_ip):
        settings = _make_dummy_settings()
        report = run_network_diagnostic(settings=settings)

        assert report.position == NetworkPosition.CORPORATE_LAN
        assert report.ready_for_live_test is True
        assert report.vpn_detected is False

    @patch("network_diag.get_local_ip", return_value="192.168.1.50")
    @patch("network_diag.detect_active_vpn_interfaces", return_value=[])
    @patch("network_diag.check_dns", return_value=True)
    @patch("network_diag.check_tcp_port")
    def test_run_network_diagnostic_public_internet_only(self, mock_tcp, mock_dns, _mock_vpn, _mock_ip):
        def tcp_side_effect(host, port, timeout=3.0):
            return bool(host == "1.1.1.1" and port == 53)

        mock_tcp.side_effect = tcp_side_effect
        report = run_network_diagnostic()

        assert report.position == NetworkPosition.PUBLIC_INTERNET
        assert report.ready_for_live_test is False

    @patch("network_diag.get_local_ip", return_value="10.20.30.40")
    @patch("network_diag.detect_active_vpn_interfaces", return_value=["Tailscale"])
    @patch("network_diag.check_dns", return_value=True)
    @patch("network_diag.check_tcp_port")
    def test_run_network_diagnostic_office_partial_cmp(self, mock_tcp, mock_dns, _mock_vpn, _mock_ip):
        def tcp_side_effect(host, port, timeout=3.0):
            return host == "mail.company.local" and port == 993

        mock_tcp.side_effect = tcp_side_effect
        settings = _make_dummy_settings()
        report = run_network_diagnostic(settings=settings)

        assert report.position == NetworkPosition.OFFICE_NETWORK_PARTIAL_CMP
        assert report.ready_for_live_test is False
        assert report.tcp_status["mail.company.local:993"] is True
        assert report.tcp_status["ep.iotcc.telkomsel.com:443"] is False

    @patch("network_diag.get_local_ip", return_value="127.0.0.1")
    @patch("network_diag.detect_active_vpn_interfaces", return_value=[])
    @patch("network_diag.check_dns", return_value=False)
    @patch("network_diag.check_tcp_port", return_value=False)
    def test_run_network_diagnostic_offline(self, mock_tcp, mock_dns, _mock_vpn, _mock_ip):
        report = run_network_diagnostic()

        assert report.position == NetworkPosition.OFFLINE
        assert report.ready_for_live_test is False

