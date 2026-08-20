"""Configuration loading and validation."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.resolve()
EXACT_OTP_SUBJECT = "CMP - YOUR TOKEN"
APPROVED_CMP_HOST = "ep.iotcc.telkomsel.com"
DEFAULT_RUN_START_TIMEZONE = "Asia/Jakarta"
APPROVED_WARP_TRACE_HOSTS = {"cloudflare.com", "www.cloudflare.com"}


class ConfigError(Exception):
    """Raised for configuration errors."""


class SecretValue:
    """Wrapper for secret values that redacts them in logs/str/repr."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "***REDACTED***"

    def __repr__(self) -> str:
        return f"SecretValue({self!s})"


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated application settings."""

    cas_url: str
    cmp_products_url: str
    cmp_dashboard_url: str
    cmp_username: SecretValue
    cmp_password: SecretValue
    imap_host: str
    imap_port: int
    imap_username: SecretValue
    imap_password: SecretValue
    imap_tls_mode: str
    imap_verify_tls: bool
    imap_mailbox: str
    otp_subject: str
    otp_poll_interval_seconds: int
    otp_timeout_seconds: int
    run_start_timezone: str
    browser_timeout_ms: int
    navigation_timeout_ms: int
    otp_form_timeout_ms: int
    refresh_interval_seconds: int
    otp_clock_skew_tolerance_seconds: int
    recovery_retry_limit: int
    recovery_backoff_seconds: int
    headless: bool
    firefox_executable_path: Path | None
    runtime_artifact_dir: Path | None
    browser_storage_state_path: Path | None
    log_level: str
    checkpoint_trac_path: Path | str | None = None
    checkpoint_site: str = "VPN-CORP"
    checkpoint_gateway_name: str = "CORP-GW01"
    checkpoint_gateway_ip: str | None = None
    checkpoint_auth_mode: str = "client_managed"
    checkpoint_allow_interactive: bool = False
    checkpoint_username: SecretValue | None = None
    checkpoint_password: SecretValue | None = None
    warp_cli_path: Path | str | None = None
    warp_variant: str = "consumer"
    warp_mode: str = "proxy"
    warp_proxy_port: int = 40000
    warp_allow_dns_only: bool = False
    warp_reuse_existing: bool = True
    warp_disconnect_on_exit: bool = True
    warp_trace_url: str = "https://www.cloudflare.com/cdn-cgi/trace"
    office_network_probe_host: str = "mail.company.local"
    office_network_probe_port: int = 993
    office_network_probe_secondary_url: str | None = None
    office_network_probe_timeout_seconds: float = 10.0
    checkpoint_connect_timeout_seconds: float = 60.0
    checkpoint_status_poll_interval_seconds: float = 2.0
    checkpoint_retry_limit: int = 3
    checkpoint_disconnect_timeout_seconds: float = 30.0
    warp_connect_timeout_seconds: float = 60.0
    warp_status_poll_interval_seconds: float = 2.0
    warp_retry_limit: int = 3
    warp_trace_timeout_seconds: float = 15.0
    dashboard_retry_limit: int = 3
    auth_cycle_retry_limit: int = 3
    viewport_width: int = 1920
    viewport_height: int = 1080
    viewport_auto: bool = False
    page_zoom_percent: int = 67


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _environment(
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> dict[str, str]:
    """Load environment values without implicitly reading a .env beside tests."""
    values: dict[str, str] = {}
    if env_file:
        path = Path(env_file)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = _strip_quotes(value.strip())
    if env is None:
        values.update(os.environ)
    else:
        values.update(env)
    return values


def _value(values: Mapping[str, str], key: str) -> str:
    if key not in values:
        raise ConfigError(f"Missing required setting: {key}")
    value = _strip_quotes(str(values[key]).strip())
    if not value:
        raise ConfigError(f"Required setting '{key}' must not be empty or whitespace")
    return value


def _optional(values: Mapping[str, str], key: str, default: str) -> str:
    return _strip_quotes(str(values.get(key, default)).strip())


def _boolean(values: Mapping[str, str], key: str, default: bool = True) -> bool:
    value = _optional(values, key, str(default)).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean value (true/false, yes/no, on/off, 1/0)")


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = _optional(values, key, str(default))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return parsed


def _positive_float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = _optional(values, key, str(default))
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive number") from exc
    if parsed <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return parsed


def _external_path(path: Path, setting: str) -> Path:
    """Reject paths that resolve inside the repository workspace."""
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return resolved
    raise ConfigError(f"{setting} must be outside the repository workspace")


def _firefox_executable_path(values: Mapping[str, str]) -> Path | None:
    configured = _optional(values, "FIREFOX_EXECUTABLE_PATH", "")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise ConfigError("FIREFOX_EXECUTABLE_PATH must be an absolute path")
    path = path.resolve()
    if not path.is_file():
        raise ConfigError("FIREFOX_EXECUTABLE_PATH must be an existing regular file")
    return _external_path(path, "FIREFOX_EXECUTABLE_PATH")


def _configured_executable(values: Mapping[str, str], key: str) -> Path | str | None:
    configured = _optional(values, key, "")
    if not configured:
        return None
    path = Path(configured)
    return path.resolve() if path.is_absolute() else configured


def _artifact_dir(values: Mapping[str, str]) -> Path:
    configured = _optional(values, "RUNTIME_ARTIFACT_DIR", "")
    path = Path(configured) if configured else Path(tempfile.gettempdir()) / "gmf-cmp-monitor" / "artifacts"
    return _external_path(path, "RUNTIME_ARTIFACT_DIR")


def _validate_cmp_url(url: str, setting_name: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError(f"{setting_name} must be an HTTPS URL")
        if parsed.username or parsed.password:
            raise ConfigError(f"{setting_name} must not contain embedded credentials")
        if parsed.hostname != APPROVED_CMP_HOST:
            raise ConfigError(f"{setting_name} must be on host {APPROVED_CMP_HOST}")
        if ":" in parsed.netloc:
            raise ConfigError(f"{setting_name} must not specify a port")
        if parsed.port is not None:
            raise ConfigError(f"{setting_name} must not specify a port")
    except ValueError as exc:
        raise ConfigError(f"{setting_name} must not specify a port") from exc
    return url


def _validate_timezone(tz_name: str) -> str:
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Invalid timezone '{tz_name}': {exc}") from exc
    return tz_name


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> Settings:
    """Load and validate settings from environment and an optional dotenv file."""
    values = _environment(env, env_file)
    cas_url = _validate_cmp_url(_value(values, "CMP_CAS_URL"), "CMP_CAS_URL")
    cmp_products_url = _validate_cmp_url(_value(values, "CMP_PRODUCTS_URL"), "CMP_PRODUCTS_URL")
    cmp_dashboard_url = _validate_cmp_url(_value(values, "CMP_DASHBOARD_URL"), "CMP_DASHBOARD_URL")
    tls_mode = _optional(values, "IMAP_TLS_MODE", "imaps").lower()
    if tls_mode not in {"imaps", "starttls"}:
        raise ConfigError("IMAP_TLS_MODE must be either imaps or starttls")
    imap_verify_tls = _boolean(values, "IMAP_VERIFY_TLS", True)
    subject = _optional(values, "OTP_SUBJECT", EXACT_OTP_SUBJECT)
    if subject != EXACT_OTP_SUBJECT:
        raise ConfigError("OTP_SUBJECT must exactly match the approved CMP subject")
    run_start_timezone = _validate_timezone(_optional(values, "RUN_START_TIMEZONE", DEFAULT_RUN_START_TIMEZONE))
    storage_state = _optional(values, "BROWSER_STORAGE_STATE_PATH", "")
    storage_state_path = _external_path(Path(storage_state), "BROWSER_STORAGE_STATE_PATH") if storage_state else None
    log_level = _optional(values, "LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
    imap_host = _optional(values, "IMAP_HOST", "mail.company.local")
    if not imap_host:
        raise ConfigError("IMAP_HOST must not be empty")
    imap_mailbox = _optional(values, "IMAP_MAILBOX", "INBOX")
    if not imap_mailbox:
        raise ConfigError("IMAP_MAILBOX must not be empty")
    checkpoint_site = _optional(values, "CHECKPOINT_SITE", "VPN-CORP")
    if not checkpoint_site:
        raise ConfigError("CHECKPOINT_SITE must not be empty")
    checkpoint_gateway_name = _optional(values, "CHECKPOINT_GATEWAY_NAME", "CORP-GW01")
    checkpoint_trac_path = _configured_executable(values, "CHECKPOINT_TRAC_PATH")
    if checkpoint_trac_path is None:
        checkpoint_trac_path = _configured_executable(values, "CHECKPOINT_CLI_PATH")
    checkpoint_auth_mode = _optional(values, "CHECKPOINT_AUTH_MODE", "client_managed").lower()
    if checkpoint_auth_mode not in {"client_managed", "credentials"}:
        raise ConfigError("CHECKPOINT_AUTH_MODE must be client_managed or credentials")
    if _boolean(values, "CHECKPOINT_ALLOW_INTERACTIVE", False):
        raise ConfigError("CHECKPOINT_ALLOW_INTERACTIVE must remain false; interactive VPN is disabled")
    checkpoint_username: SecretValue | None = None
    checkpoint_password: SecretValue | None = None
    if checkpoint_auth_mode == "credentials":
        checkpoint_username = SecretValue(_value(values, "CHECKPOINT_USERNAME"))
        checkpoint_password = SecretValue(_value(values, "CHECKPOINT_PASSWORD"))
    warp_variant = _optional(values, "WARP_VARIANT", "consumer").lower()
    if warp_variant != "consumer":
        raise ConfigError("WARP_VARIANT must be consumer")
    warp_mode = _optional(values, "WARP_MODE", "proxy").lower()
    if warp_mode not in {"warp", "proxy"}:
        raise ConfigError("WARP_MODE must be warp or proxy")
    warp_proxy_port = _positive_int(values, "WARP_PROXY_PORT", 40000)
    if not (1024 <= warp_proxy_port <= 65535):
        raise ConfigError("WARP_PROXY_PORT must be between 1024 and 65535")
    if _boolean(values, "WARP_ALLOW_DNS_ONLY", False):
        raise ConfigError("WARP_ALLOW_DNS_ONLY must remain false")
    warp_trace_url = _optional(values, "WARP_TRACE_URL", "https://www.cloudflare.com/cdn-cgi/trace")
    trace = urlparse(warp_trace_url)
    if trace.scheme != "https" or trace.hostname not in APPROVED_WARP_TRACE_HOSTS or trace.port is not None:
        raise ConfigError("WARP_TRACE_URL must use an approved HTTPS Cloudflare host")
    probe_host = _optional(values, "OFFICE_NETWORK_PROBE_HOST", imap_host)
    if not probe_host:
        raise ConfigError("OFFICE_NETWORK_PROBE_HOST must not be empty")
    secondary_url = _optional(values, "OFFICE_NETWORK_PROBE_SECONDARY_URL", "") or None
    if secondary_url:
        secondary = urlparse(secondary_url)
        if secondary.scheme != "https" or not secondary.hostname:
            raise ConfigError("OFFICE_NETWORK_PROBE_SECONDARY_URL must be an HTTPS URL")
    viewport_width = _positive_int(values, "VIEWPORT_WIDTH", 1920)
    viewport_height = _positive_int(values, "VIEWPORT_HEIGHT", 1080)
    viewport_auto = _boolean(values, "VIEWPORT_AUTO", False)
    page_zoom_percent = _positive_int(values, "PAGE_ZOOM_PERCENT", 67)
    if not (25 <= page_zoom_percent <= 200):
        raise ConfigError("PAGE_ZOOM_PERCENT must be between 25 and 200")
    return Settings(
        cas_url=cas_url,
        cmp_products_url=cmp_products_url,
        cmp_dashboard_url=cmp_dashboard_url,
        cmp_username=SecretValue(_value(values, "CMP_USERNAME")),
        cmp_password=SecretValue(_value(values, "CMP_PASSWORD")),
        imap_host=imap_host,
        imap_port=_positive_int(values, "IMAP_PORT", 993),
        imap_username=SecretValue(_value(values, "IMAP_USERNAME")),
        imap_password=SecretValue(_value(values, "IMAP_PASSWORD")),
        imap_tls_mode=tls_mode,
        imap_verify_tls=imap_verify_tls,
        imap_mailbox=imap_mailbox,
        otp_subject=subject,
        otp_poll_interval_seconds=_positive_int(values, "OTP_POLL_INTERVAL_SECONDS", 2),
        otp_timeout_seconds=_positive_int(values, "OTP_TIMEOUT_SECONDS", 120),
        run_start_timezone=run_start_timezone,
        browser_timeout_ms=_positive_int(values, "BROWSER_TIMEOUT_MS", 30000),
        navigation_timeout_ms=_positive_int(values, "NAVIGATION_TIMEOUT_MS", 90000),
        otp_form_timeout_ms=_positive_int(values, "OTP_FORM_TIMEOUT_MS", 60000),
        otp_clock_skew_tolerance_seconds=_positive_int(values, "OTP_CLOCK_SKEW_TOLERANCE_SECONDS", 120),
        refresh_interval_seconds=_positive_int(values, "REFRESH_INTERVAL_SECONDS", 60),
        recovery_retry_limit=_positive_int(values, "RECOVERY_RETRY_LIMIT", 3),
        recovery_backoff_seconds=_positive_int(values, "RECOVERY_BACKOFF_SECONDS", 5),
        headless=_boolean(values, "HEADLESS", False),
        firefox_executable_path=_firefox_executable_path(values),
        runtime_artifact_dir=_artifact_dir(values),
        browser_storage_state_path=storage_state_path,
        log_level=log_level,
        checkpoint_trac_path=checkpoint_trac_path,
        checkpoint_site=checkpoint_site,
        checkpoint_gateway_name=checkpoint_gateway_name,
        checkpoint_gateway_ip=_optional(values, "CHECKPOINT_GATEWAY_IP", "") or None,
        checkpoint_auth_mode=checkpoint_auth_mode,
        checkpoint_allow_interactive=False,
        checkpoint_username=checkpoint_username,
        checkpoint_password=checkpoint_password,
        warp_cli_path=_configured_executable(values, "WARP_CLI_PATH"),
        warp_variant=warp_variant,
        warp_mode=warp_mode,
        warp_proxy_port=warp_proxy_port,
        warp_allow_dns_only=False,
        warp_reuse_existing=_boolean(values, "WARP_REUSE_EXISTING", True),
        warp_disconnect_on_exit=_boolean(values, "WARP_DISCONNECT_ON_EXIT", True),
        warp_trace_url=warp_trace_url,
        office_network_probe_host=probe_host,
        office_network_probe_port=_positive_int(values, "OFFICE_NETWORK_PROBE_PORT", 993),
        office_network_probe_secondary_url=secondary_url,
        office_network_probe_timeout_seconds=_positive_float(values, "OFFICE_NETWORK_PROBE_TIMEOUT_SECONDS", 10.0),
        checkpoint_connect_timeout_seconds=_positive_float(values, "CHECKPOINT_CONNECT_TIMEOUT_SECONDS", 60.0),
        checkpoint_status_poll_interval_seconds=_positive_float(values, "CHECKPOINT_STATUS_POLL_INTERVAL_SECONDS", 2.0),
        checkpoint_retry_limit=_positive_int(values, "CHECKPOINT_RETRY_LIMIT", 3),
        checkpoint_disconnect_timeout_seconds=_positive_float(values, "CHECKPOINT_DISCONNECT_TIMEOUT_SECONDS", 30.0),
        warp_connect_timeout_seconds=_positive_float(values, "WARP_CONNECT_TIMEOUT_SECONDS", 60.0),
        warp_status_poll_interval_seconds=_positive_float(values, "WARP_STATUS_POLL_INTERVAL_SECONDS", 2.0),
        warp_retry_limit=_positive_int(values, "WARP_RETRY_LIMIT", 3),
        warp_trace_timeout_seconds=_positive_float(values, "WARP_TRACE_TIMEOUT_SECONDS", 15.0),
        dashboard_retry_limit=_positive_int(values, "DASHBOARD_RETRY_LIMIT", 3),
        auth_cycle_retry_limit=_positive_int(values, "AUTH_CYCLE_RETRY_LIMIT", 3),
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        viewport_auto=viewport_auto,
        page_zoom_percent=page_zoom_percent,
    )
