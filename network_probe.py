"""Network reachability checks used before changing VPN state."""

from __future__ import annotations

import logging
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


class NetworkProbeError(Exception):
    """Raised when a reachability probe cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Sanitized result of a host reachability probe."""

    reachable: bool
    stage: str


class ImapReachabilityProbe:
    """Perform DNS, TCP, and TLS checks without authenticating to IMAP."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout_seconds: float,
        *,
        getaddrinfo: Callable[..., list[tuple]] | None = None,
        socket_factory: Callable[..., socket.socket] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._getaddrinfo = getaddrinfo or socket.getaddrinfo
        self._socket_factory = socket_factory or socket.socket
        self._ssl_context = ssl_context or ssl.create_default_context()

    def check(self) -> ProbeResult:
        """Return reachability and the last successful stage without raw errors."""
        try:
            addresses = self._getaddrinfo(
                self.host,
                self.port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, socket.gaierror) as exc:
            log.info("IMAP reachability probe failed at DNS (%s)", type(exc).__name__)
            return ProbeResult(False, "dns")

        if not addresses:
            log.info("IMAP reachability probe returned no addresses")
            return ProbeResult(False, "dns")

        last_stage = "dns"
        for family, socktype, proto, _canonname, sockaddr in addresses:
            raw_socket = None
            tls_socket = None
            try:
                raw_socket = self._socket_factory(family, socktype, proto)
                raw_socket.settimeout(self.timeout_seconds)
                raw_socket.connect(sockaddr)
                last_stage = "tcp"
                tls_socket = self._ssl_context.wrap_socket(
                    raw_socket,
                    server_hostname=self.host,
                )
                last_stage = "tls"
                return ProbeResult(True, last_stage)
            except (OSError, ssl.SSLError) as exc:
                log.info(
                    "IMAP reachability probe failed at %s (%s)",
                    last_stage,
                    type(exc).__name__,
                )
            finally:
                if tls_socket is not None:
                    tls_socket.close()
                elif raw_socket is not None:
                    raw_socket.close()

        return ProbeResult(False, last_stage)

    def is_reachable(self) -> bool:
        """Return whether DNS, TCP, and TLS all succeeded."""
        return self.check().reachable
