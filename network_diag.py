"""Read-only network position and IP diagnostic module.

Provides non-destructive, read-only network diagnostics to assess connectivity
for CMP Portal and IMAP endpoints without altering VPN state or adapter configuration.
Guarantees:
- Zero mutation of system network or VPN settings (Check Point, Cloudflare WARP, etc.).
- Immediate cleanup of all run-owned socket connections.
- Strict redaction: No credentials, OTPs, trace bodies, or raw command dumps exposed.
"""

from __future__ import annotations

import enum
import logging
import re
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config import APPROVED_CMP_HOST, Settings
from vpn import CheckPointClient, CheckPointError

log = logging.getLogger(__name__)

# Known VPN provider patterns (matched against interface descriptions)
_VPN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Check Point", re.compile(r"check\s*point|cpvirtual|cpep", re.IGNORECASE)),
    ("Cloudflare WARP", re.compile(r"cloudflare|warp", re.IGNORECASE)),
    ("Cisco AnyConnect", re.compile(r"cisco|anyconnect", re.IGNORECASE)),
    ("WireGuard", re.compile(r"wireguard", re.IGNORECASE)),
    ("OpenVPN", re.compile(r"openvpn|tap-windows", re.IGNORECASE)),
    ("Tailscale", re.compile(r"tailscale", re.IGNORECASE)),
    ("Fortinet", re.compile(r"fortinet|forticlient", re.IGNORECASE)),
)

# Common public canary host to test general internet reachability
_PUBLIC_CANARY_HOST = "1.1.1.1"
_PUBLIC_CANARY_PORT = 53


class NetworkPosition(enum.Enum):
    """Classified network position."""

    CORPORATE_LAN = "CORPORATE_LAN"
    VPN_CONNECTED = "VPN_CONNECTED"
    OFFICE_NETWORK_PARTIAL_CMP = "OFFICE_NETWORK_PARTIAL_CMP"
    PUBLIC_INTERNET = "PUBLIC_INTERNET"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NetworkDiagnosticReport:
    """Immutable result of read-only network diagnostic probe."""

    local_ip: str
    dns_status: dict[str, bool]
    tcp_status: dict[str, bool]
    vpn_detected: bool
    vpn_providers: list[str] = field(default_factory=list)
    checkpoint_connected: bool | None = None
    position: NetworkPosition = NetworkPosition.UNKNOWN
    ready_for_live_test: bool = False
    summary: str = ""


def sanitize_diagnostic_output(text: str) -> str:
    """Strip any accidental passwords, tokens, OTPs, or auth headers."""
    if not text:
        return ""
    text = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***REDACTED***@", text)
    text = re.sub(r"(password|token|otp|secret|key)=([^&\s]+)", r"\1=***REDACTED***", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{6,8}\b", "***REDACTED_OTP***", text)
    return text


def check_dns(host: str, timeout: float = 3.0) -> bool:
    """Safe read-only DNS lookup for a given hostname."""
    if not host:
        return False
    try:
        res = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return bool(res)
    except (socket.gaierror, socket.herror, OSError):
        return False


def check_tcp_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """Safe read-only TCP connect probe.

    Guarantees run-owned socket is immediately closed.
    Never alters socket options or system route table.
    """
    if not host or port <= 0:
        return False
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001, S110
                pass


def get_local_ip() -> str:
    """Safely obtain the primary local IP address using a UDP route lookup."""
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except Exception:  # noqa: BLE001
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:  # noqa: BLE001
            return "127.0.0.1"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001, S110
                pass


def get_public_ip(*, opener: Callable[..., object] | None = None, timeout: float = 5.0) -> str | None:
    """Fetch the public IP from the approved Cloudflare trace endpoint.

    The value is returned to the caller for ephemeral display only. This helper
    never logs or persists the response body.
    """
    request = Request("https://www.cloudflare.com/cdn-cgi/trace", method="GET")
    open_url = opener or urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            if getattr(response, "status", 200) < 200 or getattr(response, "status", 200) >= 300:
                return None
            body = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    for line in body.splitlines():
        if line.startswith("ip="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def detect_active_vpn_interfaces(timeout: float = 2.0) -> list[str]:
    """Read-only scan of network interfaces to detect active VPN adapters.

    Does not modify any network settings. Output is parsed only for known provider names;
    raw command output is never stored or exposed.
    """
    detected: list[str] = []
    try:
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags,
                check=False,
            )
            output = res.stdout or ""
        else:
            res = subprocess.run(
                ["ip", "link", "show"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = res.stdout or ""

        for provider, pattern in _VPN_PATTERNS:
            if pattern.search(output) and provider not in detected:
                detected.append(provider)
    except Exception:  # noqa: BLE001, S110
        pass

    return detected


def run_network_diagnostic(
    settings: Settings | None = None,
    hosts_to_check: list[tuple[str, int]] | None = None,
    timeout: float = 3.0,
    *,
    checkpoint_client: CheckPointClient | None = None,
) -> NetworkDiagnosticReport:
    """Execute read-only network position and IP diagnostic.

    Preserves pre-run VPN / WARP state; cleans up all run-owned socket handles.
    Returns structured NetworkDiagnosticReport.
    """
    targets: list[tuple[str, int]] = []

    if hosts_to_check is not None:
        targets = list(hosts_to_check)
    elif settings is not None:
        parsed_cas = urlparse(settings.cas_url)
        cas_host = parsed_cas.hostname or APPROVED_CMP_HOST
        cas_port = parsed_cas.port or 443
        targets.append((cas_host, cas_port))
        targets.append((settings.imap_host, settings.imap_port))
    else:
        targets = [
            (APPROVED_CMP_HOST, 443),
            ("mail.gmf-aeroasia.co.id", 993),
        ]

    local_ip = get_local_ip()
    vpn_providers = detect_active_vpn_interfaces(timeout=min(timeout, 2.0))
    vpn_detected = len(vpn_providers) > 0
    checkpoint_connected: bool | None = None
    status_client = checkpoint_client
    if status_client is None and settings is not None:
        status_client = CheckPointClient(settings)
    if status_client is not None:
        try:
            checkpoint_connected = status_client.status().connected
        except CheckPointError:
            checkpoint_connected = None

    dns_status: dict[str, bool] = {}
    tcp_status: dict[str, bool] = {}

    for host, port in targets:
        if host not in dns_status:
            dns_status[host] = check_dns(host, timeout=timeout)
        endpoint_key = f"{host}:{port}"
        tcp_status[endpoint_key] = check_tcp_port(host, port, timeout=timeout)

    canary_tcp = check_tcp_port(_PUBLIC_CANARY_HOST, _PUBLIC_CANARY_PORT, timeout=timeout)

    all_targets_reachable = len(tcp_status) > 0 and all(tcp_status.values())
    any_target_reachable = any(tcp_status.values())
    imap_endpoint = f"{settings.imap_host}:{settings.imap_port}" if settings is not None else "mail.gmf-aeroasia.co.id:993"
    cmp_endpoint = f"{cas_host}:{cas_port}" if settings is not None else f"{APPROVED_CMP_HOST}:443"
    imap_reachable = tcp_status.get(imap_endpoint, False)
    cmp_reachable = tcp_status.get(cmp_endpoint, False)

    if all_targets_reachable:
        if checkpoint_connected is True or vpn_detected:
            position = NetworkPosition.VPN_CONNECTED
        else:
            position = NetworkPosition.CORPORATE_LAN
        ready_for_live_test = True
    elif imap_reachable and not cmp_reachable:
        position = NetworkPosition.OFFICE_NETWORK_PARTIAL_CMP
        ready_for_live_test = False
    elif any_target_reachable or canary_tcp:
        position = NetworkPosition.PUBLIC_INTERNET
        ready_for_live_test = False
    elif not any(dns_status.values()) and not canary_tcp:
        position = NetworkPosition.OFFLINE
        ready_for_live_test = False
    else:
        position = NetworkPosition.UNKNOWN
        ready_for_live_test = False

    summary_lines = [
        f"Network Position: {position.value}",
        f"Local IP: {local_ip}",
        f"VPN Detected: {vpn_detected}" + (f" ({', '.join(vpn_providers)})" if vpn_providers else ""),
        f"Live Test Ready: {ready_for_live_test}",
        "Endpoints: " + ", ".join(f"{ep}={'OK' if ok else 'FAIL'}" for ep, ok in tcp_status.items()),
    ]
    summary = "; ".join(summary_lines)
    sanitized_summary = sanitize_diagnostic_output(summary)

    return NetworkDiagnosticReport(
        local_ip=local_ip,
        dns_status=dns_status,
        tcp_status=tcp_status,
        vpn_detected=vpn_detected,
        vpn_providers=vpn_providers,
        checkpoint_connected=checkpoint_connected,
        position=position,
        ready_for_live_test=ready_for_live_test,
        summary=sanitized_summary,
    )

