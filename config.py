"""Configuration loading and validation.

Settings are loaded from environment variables and/or an external .env file.
All secrets are wrapped in SecretValue so they are never logged or printed.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.resolve()

EXACT_OTP_SUBJECT = "CMP - YOUR TOKEN"
APPROVED_CMP_HOST = "ep.iotcc.telkomsel.com"
DEFAULT_RUN_START_TIMEZONE = "Asia/Jakarta"


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
        return f"SecretValue({str(self)})"


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
    runtime_artifact_dir: Path | None
    browser_storage_state_path: Path | None
    log_level: str


def _environment(
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> dict[str, str]:
    """Load environment variables from .env file and/or provided mapping.

    Precedence (lowest to highest): defaults applied by load_settings, values
    from the .env file, process environment variables, and finally the explicit
    env mapping. Process environment variables therefore override .env values,
    and an explicit env mapping overrides both.
    """
    if not env_file:
        # Check current directory then project root for .env
        for candidate in [Path(".env"), PROJECT_ROOT / ".env"]:
            if candidate.exists():
                env_file = candidate
                break

    values: dict[str, str] = {}
    if env_file:
        path = Path(env_file)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()
    # Process environment variables override .env values.
    for key, value in os.environ.items():
        values[key] = value
    # Explicit env mapping has the highest precedence.
    if env:
        values.update(env)
    return values


def _value(values: Mapping[str, str], key: str) -> str:
    if key not in values:
        raise ConfigError(f"Missing required setting: {key}")
    val = values[key]
    if not val or not val.strip():
        raise ConfigError(f"Required setting '{key}' must not be empty or whitespace")
    return val.strip()


def _optional(values: Mapping[str, str], key: str, default: str) -> str:
    val = values.get(key, default)
    if val is None:
        return default
    return val.strip()


def _boolean(values: Mapping[str, str], key: str, default: bool = True) -> bool:
    val = values.get(key)
    if val is None:
        return default
    val_lower = val.lower()
    if val_lower in {"1", "true", "yes", "on"}:
        return True
    elif val_lower in {"0", "false", "no", "off"}:
        return False
    else:
        # Do not include the actual invalid value in the error message to prevent leakage
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
    """Reject paths that resolve inside the repository workspace.

    Relative paths are resolved against PROJECT_ROOT so in-repo relative
    values are rejected regardless of the current working directory.
    """
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError:
        return path
    raise ConfigError(f"{setting} must be outside the repository workspace")


def _artifact_dir(values: Mapping[str, str]) -> Path:
    configured = _optional(values, "RUNTIME_ARTIFACT_DIR", "")
    path = (
        Path(configured)
        if configured
        else Path(tempfile.gettempdir()) / "gmf-cmp-monitor" / "artifacts"
    )
    return _external_path(path, "RUNTIME_ARTIFACT_DIR")


def _validate_cmp_url(url: str, setting_name: str) -> str:
    """Validate a CMP URL: HTTPS only, approved host only, no embedded secrets."""
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ConfigError(f"Invalid URL for {setting_name}") from exc
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(f"{setting_name} must be an HTTPS URL")
    # Check for embedded credentials before the host check so that URLs such as
    # https://user:pass@ep.iotcc.telkomsel.com/ are reported as credential leaks.
    if parsed.username or parsed.password:
        raise ConfigError(f"{setting_name} must not contain embedded credentials")
    if parsed.hostname != APPROVED_CMP_HOST:
        raise ConfigError(f"{setting_name} must be on host {APPROVED_CMP_HOST}")
    if ":" in parsed.netloc:
        raise ConfigError(f"{setting_name} must not specify a port")
    try:
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
    """Load and validate settings from environment and .env file."""
    values = _environment(env, env_file)
    cas_url = _validate_cmp_url(_value(values, "CMP_CAS_URL"), "CMP_CAS_URL")
    cmp_products_url = _validate_cmp_url(_value(values, "CMP_PRODUCTS_URL"), "CMP_PRODUCTS_URL")
    cmp_dashboard_url = _validate_cmp_url(_value(values, "CMP_DASHBOARD_URL"), "CMP_DASHBOARD_URL")

    tls_mode = _optional(values, "IMAP_TLS_MODE", "imaps").lower()
    if tls_mode not in {"imaps", "starttls"}:
        raise ConfigError("IMAP_TLS_MODE must be either imaps or starttls")
    if not _boolean(values, "IMAP_VERIFY_TLS", True):
        raise ConfigError("IMAP_VERIFY_TLS must remain enabled")

    subject = _optional(values, "OTP_SUBJECT", EXACT_OTP_SUBJECT)
    if subject != EXACT_OTP_SUBJECT:
        raise ConfigError("OTP_SUBJECT must exactly match the approved CMP subject")

    run_start_timezone = _validate_timezone(
        _optional(values, "RUN_START_TIMEZONE", DEFAULT_RUN_START_TIMEZONE)
    )

    storage_state = _optional(values, "BROWSER_STORAGE_STATE_PATH", "")
    storage_state_path = None
    if storage_state:
        path = _external_path(Path(storage_state), "BROWSER_STORAGE_STATE_PATH")
        storage_state_path = path

    log_level = _optional(values, "LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

    # Validate IMAP_HOST and IMAP_MAILBOX are non-empty
    imap_host = _optional(values, "IMAP_HOST", "mail.gmf-aeroasia.co.id")
    if not imap_host:
        raise ConfigError("IMAP_HOST must not be empty")
    imap_mailbox = _optional(values, "IMAP_MAILBOX", "INBOX")
    if not imap_mailbox:
        raise ConfigError("IMAP_MAILBOX must not be empty")

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
        imap_verify_tls=True,
        imap_mailbox=imap_mailbox,
        otp_subject=subject,
        otp_poll_interval_seconds=_positive_int(values, "OTP_POLL_INTERVAL_SECONDS", 2),
        otp_timeout_seconds=_positive_int(values, "OTP_TIMEOUT_SECONDS", 120),
        run_start_timezone=run_start_timezone,
        browser_timeout_ms=_positive_int(values, "BROWSER_TIMEOUT_MS", 30000),
        navigation_timeout_ms=_positive_int(values, "NAVIGATION_TIMEOUT_MS", 60000),
        otp_form_timeout_ms=_positive_int(values, "OTP_FORM_TIMEOUT_MS", 30000),
        otp_clock_skew_tolerance_seconds=_positive_int(values, "OTP_CLOCK_SKEW_TOLERANCE_SECONDS", 120),
        refresh_interval_seconds=_positive_int(values, "REFRESH_INTERVAL_SECONDS", 60),
        recovery_retry_limit=_positive_int(values, "RECOVERY_RETRY_LIMIT", 3),
        recovery_backoff_seconds=_positive_int(values, "RECOVERY_BACKOFF_SECONDS", 5),
        headless=_boolean(values, "HEADLESS", False),
        runtime_artifact_dir=_artifact_dir(values),
        browser_storage_state_path=storage_state_path,
        log_level=log_level,
    )
