"""Offline tests for IMAP reachability probing."""

from __future__ import annotations

import socket

from network_probe import ImapReachabilityProbe


class FakeSocket:
    def __init__(self, calls: list[tuple], should_fail: bool = False) -> None:
        self.calls = calls
        self.should_fail = should_fail
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.calls.append(("timeout", value))

    def connect(self, address: object) -> None:
        self.calls.append(("connect", address))
        if self.should_fail:
            raise TimeoutError("network detail must not be logged")

    def close(self) -> None:
        self.closed = True
        self.calls.append(("close",))


class FakeTlsSocket(FakeSocket):
    pass


def test_probe_performs_dns_tcp_and_tls_without_authentication():
    calls: list[tuple] = []
    raw = FakeSocket(calls)
    tls = FakeTlsSocket(calls)

    def getaddrinfo(host, port, **kwargs):
        calls.append(("dns", host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]

    class Context:
        def wrap_socket(self, sock, server_hostname):
            calls.append(("tls", server_hostname))
            return tls

    result = ImapReachabilityProbe(
        "mail.example.test",
        993,
        10,
        getaddrinfo=getaddrinfo,
        socket_factory=lambda *args: raw,
        ssl_context=Context(),
    ).check()

    assert result.reachable is True
    assert result.stage == "tls"
    assert calls[0][0] == "dns"
    assert ("connect", ("mail.example.test", 993)) in calls
    assert ("tls", "mail.example.test") in calls
    assert calls[-1] == ("close",)


def test_probe_returns_false_when_dns_fails():
    result = ImapReachabilityProbe(
        "mail.example.test",
        993,
        10,
        getaddrinfo=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("secret")),
    ).check()

    assert result.reachable is False
    assert result.stage == "dns"


def test_probe_returns_false_when_tcp_fails():
    calls: list[tuple] = []
    raw = FakeSocket(calls, should_fail=True)

    result = ImapReachabilityProbe(
        "mail.example.test",
        993,
        10,
        getaddrinfo=lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("mail.example.test", 993))
        ],
        socket_factory=lambda *args: raw,
    ).check()

    assert result.reachable is False
    assert result.stage == "dns"
    assert ("close",) in calls
