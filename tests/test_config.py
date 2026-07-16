"""Tests for configuration validation."""

import os
import tempfile
from pathlib import Path

import pytest

from config import (
    ConfigError,
    EXACT_OTP_SUBJECT,
    APPROVED_CMP_HOST,
    DEFAULT_RUN_START_TIMEZONE,
    load_settings,
    SecretValue,
    Settings,
)


class TestApprovedUrls:
    """Tests for CMP URL validation."""

    def test_cas_url_must_be_https(self):
        env = {
            "CMP_CAS_URL": "http://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="HTTPS URL"):
            load_settings(env=env)

    def test_products_url_must_be_on_approved_host(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://evil.example.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match=APPROVED_CMP_HOST):
            load_settings(env=env)

    def test_dashboard_url_must_be_on_approved_host(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://evil.example.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match=APPROVED_CMP_HOST):
            load_settings(env=env)

    def test_valid_cmp_urls_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.cmp_products_url == "https://ep.iotcc.telkomsel.com/#!products"
        assert settings.cmp_dashboard_url == "https://ep.iotcc.telkomsel.com/#!dashboard"


class TestCasUrlValidation:
    """Tests for CAS URL validation using approved host validator."""

    def test_cas_url_must_be_on_approved_host(self):
        env = {
            "CMP_CAS_URL": "https://evil.example.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match=APPROVED_CMP_HOST):
            load_settings(env=env)

    def test_cas_url_must_be_https(self):
        env = {
            "CMP_CAS_URL": "http://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="HTTPS URL"):
            load_settings(env=env)

    def test_cas_url_rejects_embedded_credentials(self):
        env = {
            "CMP_CAS_URL": "https://user:pass@ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="credentials"):
            load_settings(env=env)

    def test_cas_url_rejects_malformed_url(self):
        env = {
            "CMP_CAS_URL": "not-a-url",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="HTTPS URL"):
            load_settings(env=env)

    def test_valid_cas_url_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com%2Fcas%2Foauth2.0%2FcallbackAuthorize%3Fclient_id%3DenterprisePortal%26redirect_uri%3Dhttps%253A%252F%252Fep.iotcc.telkomsel.com%26response_type%3Dcode%26client_name%3DCasOAuthClient",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.cas_url == env["CMP_CAS_URL"]


class TestTimezoneValidation:
    """Tests for timezone validation."""

    def test_asia_jakarta_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.run_start_timezone == "Asia/Jakarta"

    def test_utc_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "RUN_START_TIMEZONE": "UTC",
        }
        settings = load_settings(env=env)
        assert settings.run_start_timezone == "UTC"

    def test_invalid_timezone_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "RUN_START_TIMEZONE": "Invalid/Timezone",
        }
        with pytest.raises(ConfigError, match="timezone"):
            load_settings(env=env)

    def test_defaults_to_asia_jakarta(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.run_start_timezone == "Asia/Jakarta"


class TestSecretPathProtection:
    def test_storage_state_inside_repo_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "BROWSER_STORAGE_STATE_PATH": "storage_state/browser.json",
        }
        with pytest.raises(ConfigError, match="outside"):
            load_settings(env=env)

    def test_storage_path_outside_repo_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "BROWSER_STORAGE_STATE_PATH": str(tempfile.gettempdir()),
        }
        settings = load_settings(env=env)
        assert settings.browser_storage_state_path is not None


class TestMandatoryTls:
    def test_tls_verification_cannot_be_disabled(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_VERIFY_TLS": "false",
        }
        with pytest.raises(ConfigError, match="IMAP_VERIFY_TLS"):
            load_settings(env=env)


class TestSecretRedaction:
    def test_secret_str_is_redacted(self):
        secret = SecretValue("sensitive")
        assert str(secret) == "***REDACTED***"

    def test_secret_repr_is_redacted(self):
        secret = SecretValue("sensitive")
        assert repr(secret) == "SecretValue(***REDACTED***)"

    def test_secret_value_accessible(self):
        secret = SecretValue("sensitive")
        assert secret.get_secret_value() == "sensitive"


class TestOtpSubjectValidation:
    def test_exact_subject_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "OTP_SUBJECT": EXACT_OTP_SUBJECT,
        }
        settings = load_settings(env=env)
        assert settings.otp_subject == EXACT_OTP_SUBJECT

    def test_modified_subject_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "OTP_SUBJECT": "DIFFERENT SUBJECT",
        }
        with pytest.raises(ConfigError, match="OTP_SUBJECT"):
            load_settings(env=env)


class TestBrowserSettings:
    def test_headless_default(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.headless is False

    def test_headless_enabled(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "HEADLESS": "true",
        }
        settings = load_settings(env=env)
        assert settings.headless is True


class TestRuntimeSettings:
    def test_runtime_artifact_dir(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        settings = load_settings(env=env)
        assert settings.runtime_artifact_dir is not None
        assert settings.refresh_interval_seconds == 60
        assert settings.recovery_retry_limit == 3
        assert settings.recovery_backoff_seconds == 5


class TestRequiredValueValidation:
    """Tests for _value() rejecting missing AND whitespace-only required values."""

    def test_whitespace_only_cmp_username_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "   ",  # whitespace only
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="CMP_USERNAME.*must not be empty"):
            load_settings(env=env)

    def test_whitespace_only_cmp_password_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "\t\n",  # whitespace only
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="CMP_PASSWORD.*must not be empty"):
            load_settings(env=env)

    def test_whitespace_only_imap_username_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "  ",  # whitespace only
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="IMAP_USERNAME.*must not be empty"):
            load_settings(env=env)

    def test_whitespace_only_imap_password_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": " ",  # whitespace only
        }
        with pytest.raises(ConfigError, match="IMAP_PASSWORD.*must not be empty"):
            load_settings(env=env)


class TestOptionalValueTrimming:
    """Tests for _optional() trimming values consistently."""

    def test_imap_host_trimmed(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_HOST": "  mail.gmf-aeroasia.co.id  ",
        }
        settings = load_settings(env=env)
        assert settings.imap_host == "mail.gmf-aeroasia.co.id"

    def test_imap_mailbox_trimmed(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_MAILBOX": "  INBOX  ",
        }
        settings = load_settings(env=env)
        assert settings.imap_mailbox == "INBOX"


class TestBooleanValidation:
    """Tests for _boolean() rejecting unknown values."""

    def test_boolean_maybe_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "HEADLESS": "maybe",
        }
        with pytest.raises(ConfigError, match="HEADLESS must be a boolean value"):
            load_settings(env=env)

    def test_boolean_invalid_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "HEADLESS": "invalid",
        }
        with pytest.raises(ConfigError, match="HEADLESS must be a boolean value"):
            load_settings(env=env)

    def test_boolean_true_values_accepted(self):
        for val in ["1", "true", "yes", "on", "True", "YES", "ON"]:
            env = {
                "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
                "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
                "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
                "CMP_USERNAME": "testuser",
                "CMP_PASSWORD": "testpass",
                "IMAP_USERNAME": "imapuser",
                "IMAP_PASSWORD": "imappass",
                "HEADLESS": val,
            }
            settings = load_settings(env=env)
            assert settings.headless is True

    def test_boolean_false_values_accepted(self):
        for val in ["0", "false", "no", "off", "False", "NO", "OFF"]:
            env = {
                "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
                "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
                "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
                "CMP_USERNAME": "testuser",
                "CMP_PASSWORD": "testpass",
                "IMAP_USERNAME": "imapuser",
                "IMAP_PASSWORD": "imappass",
                "HEADLESS": val,
            }
            settings = load_settings(env=env)
            assert settings.headless is False

    def test_boolean_invalid_value_not_leaked_in_error(self):
        secret_like_value = "my-secret-api-key-12345"
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "HEADLESS": secret_like_value,
        }
        with pytest.raises(ConfigError) as exc_info:
            load_settings(env=env)
        error_msg = str(exc_info.value)
        assert secret_like_value not in error_msg
        assert "HEADLESS must be a boolean value" in error_msg


class TestImapHostValidation:
    """Tests for IMAP_HOST validation."""

    def test_empty_imap_host_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_HOST": "",
        }
        with pytest.raises(ConfigError, match="IMAP_HOST must not be empty"):
            load_settings(env=env)

    def test_whitespace_imap_host_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_HOST": "   ",
        }
        with pytest.raises(ConfigError, match="IMAP_HOST must not be empty"):
            load_settings(env=env)


class TestImapMailboxValidation:
    """Tests for IMAP_MAILBOX validation."""

    def test_empty_imap_mailbox_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_MAILBOX": "",
        }
        with pytest.raises(ConfigError, match="IMAP_MAILBOX must not be empty"):
            load_settings(env=env)

    def test_whitespace_imap_mailbox_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "IMAP_MAILBOX": "   ",
        }
        with pytest.raises(ConfigError, match="IMAP_MAILBOX must not be empty"):
            load_settings(env=env)