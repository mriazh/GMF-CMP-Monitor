"""Safe subprocess adapters for Check Point Endpoint Connect and WARP."""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config import Settings

log = logging.getLogger(__name__)


class VpnError(Exception):
    """Base class for safe VPN workflow errors."""


class CheckPointError(VpnError):
    """Raised for Check Point command or state failures."""


class WarpError(VpnError):
    """Raised for WARP command or state failures."""


class WarpValidationError(WarpError):
    """Raised when WARP is not in the required full-tunnel state."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Sanitized subprocess result."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class CheckPointStatus:
    """Parsed Check Point site state."""

    site: str
    connected: bool


@dataclass(frozen=True, slots=True)
class WarpStatus:
    """Parsed WARP state."""

    connected: bool
    mode: str | None


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    check = kwargs.pop("check", False)
    return subprocess.run(*args, check=check, **kwargs)


def _result(completed: subprocess.CompletedProcess[str]) -> CommandResult:
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _run_command(
    executable: Path | str,
    args: Sequence[str],
    timeout_seconds: float,
    runner: Runner,
) -> CommandResult:
    command = [str(executable), *args]
    try:
        completed = runner(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VpnError("VPN command timed out") from exc
    except OSError as exc:
        raise VpnError("VPN command could not be started") from exc
    return _result(completed)


def parse_checkpoint_status(output: str, site: str) -> CheckPointStatus:
    """Parse one configured site's connection status without exposing output."""
    normalized_output = output.replace("\t", " ")
    site_marker = f"Conn {site}:"
    start = normalized_output.lower().find(site_marker.lower())
    if start < 0:
        return CheckPointStatus(site=site, connected=False)
    section = normalized_output[start:]
    next_conn = section.find("\nConn ", len(site_marker))
    if next_conn >= 0:
        section = section[:next_conn]
    status = ""
    for line in section.splitlines():
        if line.strip().lower().startswith("status:"):
            status = line.split(":", 1)[1].strip().lower()
            break
    return CheckPointStatus(site=site, connected=status in {"connected", "active"})


def _normalize_mode(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if cleaned in {"proxy", "warpproxy"}:
        return "proxy"
    if cleaned in {"warp", "full"}:
        return "warp"
    return cleaned


def parse_warp_status(output: str) -> WarpStatus:
    """Parse WARP status output, accepting current and JSON forms."""
    normalized = output.lower()
    connected = any(
        marker in normalized
        for marker in (
            "status update: connected",
            "status: connected",
        )
    )
    mode = None
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        raw_mode = parsed.get("mode")
        if isinstance(raw_mode, str):
            mode = _normalize_mode(raw_mode)
        raw_status = parsed.get("status")
        if isinstance(raw_status, str):
            connected = raw_status.strip().lower() == "connected"
    if mode is None:
        for line in output.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("mode:"):
                raw_mode = stripped.split(":", 1)[1].strip()
                mode = _normalize_mode(raw_mode.split()[0] if raw_mode else None)
                break
    return WarpStatus(connected=connected, mode=mode)



class CheckPointClient:
    """Control a configured Check Point site with ownership-aware auth."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._runner = runner or _default_runner
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        self.owned = False

    @property
    def executable(self) -> Path | str:
        return self.settings.checkpoint_trac_path or "trac"

    def command_info(self) -> list[str]:
        return [str(self.executable), "info", "-s", self.settings.checkpoint_site]

    def command_connect(self) -> list[str]:
        args = ["connect", "-s", self.settings.checkpoint_site]
        if self.settings.checkpoint_auth_mode == "credentials":
            if not self.settings.checkpoint_username or not self.settings.checkpoint_password:
                raise CheckPointError("Check Point credentials are required in credentials mode")
            args.extend([
                "-u",
                self.settings.checkpoint_username.get_secret_value(),
                "-p",
                self.settings.checkpoint_password.get_secret_value(),
            ])
        gateway = getattr(self.settings, "checkpoint_gateway_name", None)
        if gateway:
            args.extend(["-g", gateway])
        return [str(self.executable), *args]

    def command_disconnect(self) -> list[str]:
        args = ["disconnect"]
        gateway = getattr(self.settings, "checkpoint_gateway_name", None)
        if gateway:
            args.extend(["-g", gateway])
        return [str(self.executable), *args]

    def status(self, timeout_seconds: float | None = None) -> CheckPointStatus:
        timeout = (
            self.settings.checkpoint_connect_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            result = _run_command(
                self.executable,
                ["info", "-s", self.settings.checkpoint_site],
                timeout,
                self._runner,
            )
        except VpnError as exc:
            raise CheckPointError("Check Point status failed") from exc
        if result.returncode != 0:
            raise CheckPointError("Check Point status failed")
        return parse_checkpoint_status(result.stdout, self.settings.checkpoint_site)

    def connect(self) -> None:
        current = self.status()
        if current.connected:
            return
        connect_args = self.command_connect()[1:]
        last_error: CheckPointError | None = None
        for attempt in range(1, self.settings.checkpoint_retry_limit + 1):
            try:
                result = _run_command(
                    self.executable,
                    connect_args,
                    self.settings.checkpoint_connect_timeout_seconds,
                    self._runner,
                )
            except VpnError as exc:
                last_error = CheckPointError("Check Point connect failed")
                last_error.__cause__ = exc
            else:
                if result.returncode == 0:
                    self.owned = True
                    return
                last_error = CheckPointError("Check Point connect failed")
            if attempt < self.settings.checkpoint_retry_limit:
                self._sleep(self.settings.checkpoint_status_poll_interval_seconds)
        raise last_error or CheckPointError("Check Point connect failed")

    def wait_until_connected(self) -> None:
        deadline = self._monotonic() + self.settings.checkpoint_connect_timeout_seconds
        while True:
            if self.status().connected:
                return
            if self._monotonic() >= deadline:
                raise CheckPointError("Check Point connection timed out")
            self._sleep(self.settings.checkpoint_status_poll_interval_seconds)

    def disconnect(self) -> None:
        if not self.owned:
            return
        try:
            result = _run_command(
                self.executable,
                self.command_disconnect()[1:],
                self.settings.checkpoint_disconnect_timeout_seconds,
                self._runner,
            )
        except VpnError as exc:
            raise CheckPointError("Check Point disconnect failed") from exc
        finally:
            self.owned = False
        if result.returncode != 0:
            raise CheckPointError("Check Point disconnect failed")


class WarpClient:
    """Control consumer WARP with mode and ownership enforcement."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner | None = None,
        opener: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._runner = runner or _default_runner
        self._opener = opener or urlopen
        self._sleep = sleeper or time.sleep
        self._monotonic = monotonic or time.monotonic
        self.owned = False

    @property
    def executable(self) -> Path | str:
        return self.settings.warp_cli_path or "warp-cli"

    def _status_result(self) -> CommandResult:
        try:
            return _run_command(
                self.executable,
                ["--no-ansi", "status"],
                self.settings.warp_connect_timeout_seconds,
                self._runner,
            )
        except VpnError as exc:
            raise WarpError("WARP status failed") from exc

    def _query_mode(self) -> str | None:
        try:
            result = _run_command(
                self.executable,
                ["--no-ansi", "settings"],
                self.settings.warp_connect_timeout_seconds,
                self._runner,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    stripped = line.strip().lower()
                    if "mode:" in stripped:
                        val = stripped.split("mode:", 1)[1].strip()
                        if val:
                            return _normalize_mode(val.split()[0].strip())
        except Exception:  # noqa: BLE001, S110
            pass
        return None

    def status(self) -> WarpStatus:
        result = self._status_result()
        if result.returncode != 0:
            raise WarpError("WARP status failed")
        st = parse_warp_status(result.stdout)
        if st.connected and st.mode is None:
            mode = self._query_mode()
            if mode:
                st = WarpStatus(connected=st.connected, mode=mode)
        return st

    def connect(self) -> None:
        try:
            current = self.status()
        except WarpError:
            current = WarpStatus(connected=False, mode=None)
        if current.connected:
            if current.mode is None:
                raise WarpValidationError("WARP connected state has unknown mode")
            if current.mode != self.settings.warp_mode.lower():
                raise WarpValidationError("WARP is connected in an unsupported mode")
            if not self.settings.warp_reuse_existing:
                raise WarpError("WARP is already connected")
            if not getattr(self, "owned", False):
                self.owned = False
            return
        last_error: WarpError | None = None
        for attempt in range(1, self.settings.warp_retry_limit + 1):
            try:
                if self.settings.warp_mode.lower() == "proxy":
                    _run_command(
                        self.executable,
                        ["mode", "proxy"],
                        self.settings.warp_connect_timeout_seconds,
                        self._runner,
                    )
                    _run_command(
                        self.executable,
                        ["proxy", "port", str(self.settings.warp_proxy_port)],
                        self.settings.warp_connect_timeout_seconds,
                        self._runner,
                    )
                result = _run_command(
                    self.executable,
                    ["connect"],
                    self.settings.warp_connect_timeout_seconds,
                    self._runner,
                )
            except VpnError as exc:
                last_error = WarpError("WARP connect failed")
                last_error.__cause__ = exc
            else:
                if result.returncode == 0:
                    self.owned = True
                    return
                last_error = WarpError("WARP connect failed")
            if attempt < self.settings.warp_retry_limit:
                self._sleep(self.settings.warp_status_poll_interval_seconds)
        raise last_error or WarpError("WARP connect failed")

    def wait_until_connected(self) -> WarpStatus:
        deadline = self._monotonic() + self.settings.warp_connect_timeout_seconds
        while True:
            status = self.status()
            if status.connected:
                if status.mode is None:
                    raise WarpValidationError("WARP connected state has unknown mode")
                if status.mode != self.settings.warp_mode.lower():
                    raise WarpValidationError("WARP is connected in an unsupported mode")
                return status
            if self._monotonic() >= deadline:
                raise WarpError("WARP connection timed out")
            self._sleep(self.settings.warp_status_poll_interval_seconds)

    def _validate_socks5_proxy(self) -> str:
        port = self.settings.warp_proxy_port
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.settings.warp_trace_timeout_seconds)
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"\x05\x01\x00")
            resp = sock.recv(2)
            if resp != b"\x05\x00":
                raise WarpValidationError(f"SOCKS5 proxy handshake rejected: {resp!r}")

            domain = b"www.cloudflare.com"
            connect_req = b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + (80).to_bytes(2, "big")
            sock.sendall(connect_req)
            connect_resp = sock.recv(10)
            if len(connect_resp) < 2 or connect_resp[1] != 0:
                raise WarpValidationError("SOCKS5 proxy connection to Cloudflare failed")

            sock.sendall(
                b"GET /cdn-cgi/trace HTTP/1.1\r\n"
                b"Host: www.cloudflare.com\r\n"
                b"User-Agent: GMF-CMP-Monitor/1.0\r\n"
                b"Connection: close\r\n\r\n"
            )
            raw = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                raw += chunk
            return raw.decode("utf-8", errors="replace")
        except WarpValidationError:
            raise
        except Exception as exc:
            raise WarpValidationError("WARP SOCKS5 trace validation failed") from exc
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def validate(self) -> None:
        status = self.wait_until_connected()
        expected_mode = self.settings.warp_mode.lower()
        if status.mode != expected_mode:
            raise WarpValidationError(f"WARP is not in {expected_mode} mode")
        trace_url = self.settings.warp_trace_url
        parsed = urlparse(trace_url)
        if parsed.scheme != "https" or parsed.hostname not in {"cloudflare.com", "www.cloudflare.com"}:
            raise WarpValidationError("WARP trace URL is not approved")

        if expected_mode == "proxy":
            body = self._validate_socks5_proxy()
        else:
            request = Request(trace_url, method="GET")
            try:
                with self._opener(request, timeout=self.settings.warp_trace_timeout_seconds) as response:
                    if getattr(response, "status", 200) < 200 or getattr(response, "status", 200) >= 300:
                        raise WarpValidationError("WARP trace validation failed")
                    body = response.read().decode("utf-8", errors="replace")
            except WarpValidationError:
                raise
            except Exception as exc:
                raise WarpValidationError("WARP trace validation failed") from exc

        fields = dict(
            line.split("=", 1)
            for line in body.splitlines()
            if "=" in line
        )
        if fields.get("warp", "").strip().lower() not in {"on", "plus"}:
            raise WarpValidationError("WARP trace did not confirm tunnel")

    def disconnect(self) -> None:
        if not self.owned or not self.settings.warp_disconnect_on_exit:
            return
        try:
            result = _run_command(
                self.executable,
                ["disconnect"],
                self.settings.warp_connect_timeout_seconds,
                self._runner,
            )
        except VpnError as exc:
            raise WarpError("WARP disconnect failed") from exc
        finally:
            self.owned = False
        if result.returncode != 0:
            raise WarpError("WARP disconnect failed")


class ConnectivityController:
    """Coordinate ownership and ordered teardown of Check Point and WARP."""

    def __init__(
        self,
        settings: Settings,
        probe: object,
        checkpoint: CheckPointClient,
        warp: WarpClient,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.probe = probe
        self.checkpoint = checkpoint
        self.warp = warp
        self._sleep = sleeper or time.sleep

    def ensure_imap_reachable(self) -> None:
        if self.probe.is_reachable():
            return
        last_error: CheckPointError | None = None
        for attempt in range(1, self.settings.checkpoint_retry_limit + 1):
            try:
                self.checkpoint.connect()
                self.checkpoint.wait_until_connected()
                if self.probe.is_reachable():
                    return
                last_error = CheckPointError("IMAP is unreachable after Check Point connection")
            except CheckPointError as exc:
                last_error = exc
            if getattr(self.checkpoint, "owned", False):
                self.checkpoint.disconnect()
            if attempt < self.settings.checkpoint_retry_limit:
                self._sleep(self.settings.checkpoint_status_poll_interval_seconds)
        raise last_error or CheckPointError("Check Point connection failed")

    def prepare_warp(self) -> None:
        last_error: WarpError | None = None
        for attempt in range(1, self.settings.warp_retry_limit + 1):
            try:
                self.warp.connect()
                self.warp.validate()
                return
            except WarpError as exc:
                last_error = exc
                if getattr(self.warp, "owned", False):
                    try:
                        self.warp.disconnect()
                    except WarpError as cleanup_error:
                        last_error = cleanup_error
                if attempt < self.settings.warp_retry_limit:
                    self._sleep(self.settings.warp_status_poll_interval_seconds)
        raise last_error or WarpError("WARP preparation failed")

    def disconnect_checkpoint_after_imap(self) -> None:
        self.checkpoint.disconnect()

    def prepare_reauthentication(self) -> None:
        if getattr(self.warp, "owned", False) and getattr(self.settings, "warp_mode", "warp").lower() != "proxy":
            self.warp.disconnect()
            self._sleep(max(2.0, self.settings.warp_status_poll_interval_seconds))
            for _ in range(3):
                if self.probe.is_reachable():
                    return
                self._sleep(self.settings.warp_status_poll_interval_seconds)
        self.ensure_imap_reachable()

    def restart_auth_connectivity(self) -> None:
        self.prepare_reauthentication()

    def finish_authentication(self) -> None:
        self.disconnect_checkpoint_after_imap()
        self.prepare_warp()

    def cleanup(self) -> None:
        try:
            self.checkpoint.disconnect()
        except CheckPointError as exc:
            log.warning("Check Point cleanup failed (%s)", type(exc).__name__)
        try:
            self.warp.disconnect()
        except WarpError as exc:
            log.warning("WARP cleanup failed (%s)", type(exc).__name__)

    def close(self) -> None:
        self.cleanup()

