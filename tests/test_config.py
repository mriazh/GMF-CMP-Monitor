"""Tests for configuration validation."""

import tempfile

import pytest

from config import (
    APPROVED_CMP_HOST,
    EXACT_OTP_SUBJECT,
    PROJECT_ROOT,
    ConfigError,
    SecretValue,
    load_settings,
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

    def test_cmp_url_with_empty_port_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com:/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="must not specify a port"):
            load_settings(env=env)

    def test_cmp_url_with_malformed_port_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com:notaport/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
        }
        with pytest.raises(ConfigError, match="must not specify a port"):
            load_settings(env=env)


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


class TestEnvFileLoading:
    def test_quoted_values_are_unwrapped(self, tmp_path, monkeypatch):
        for k in (
            "CMP_CAS_URL",
            "CMP_PRODUCTS_URL",
            "CMP_DASHBOARD_URL",
            "CMP_USERNAME",
            "CMP_PASSWORD",
            "IMAP_USERNAME",
            "IMAP_PASSWORD",
            "CHECKPOINT_TRAC_PATH",
        ):
            monkeypatch.delenv(k, raising=False)
        env_file = tmp_path / "settings.env"
        env_file.write_text(
            'CMP_CAS_URL="https://ep.iotcc.telkomsel.com/cas/login"\n'
            'CMP_PRODUCTS_URL="https://ep.iotcc.telkomsel.com/#!products"\n'
            'CMP_DASHBOARD_URL="https://ep.iotcc.telkomsel.com/#!dashboard"\n'
            'CMP_USERNAME="testuser"\n'
            'CMP_PASSWORD="testpass"\n'
            'IMAP_USERNAME="imapuser"\n'
            'IMAP_PASSWORD="imappass"\n'
            'CHECKPOINT_TRAC_PATH="C:/Program Files (x86)/CheckPoint/trac.exe"\n'
        )
        settings = load_settings(env_file=env_file)
        assert settings.cmp_username.get_secret_value() == "testuser"
        assert str(settings.checkpoint_trac_path) == "C:\\Program Files (x86)\\CheckPoint\\trac.exe"

    def test_environment_loading_does_not_discover_dotenv(self, tmp_path, monkeypatch):
        for key in (
            "CMP_CAS_URL",
            "CMP_PRODUCTS_URL",
            "CMP_DASHBOARD_URL",
            "CMP_USERNAME",
            "CMP_PASSWORD",
            "IMAP_USERNAME",
            "IMAP_PASSWORD",
        ):
            monkeypatch.delenv(key, raising=False)
        dot_env = tmp_path / ".env"
        dot_env.write_text(self._minimal_env_content())
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ConfigError, match="CMP_CAS_URL"):
            load_settings()
    def test_root_dotenv_validation(self):
        """Validate the root .env file if present in the repository."""
        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            pytest.skip(".env file not present in project root")

        settings = load_settings(env_file=env_path)

        assert settings.cas_url.startswith("https://")
        assert len(settings.cmp_username.get_secret_value()) > 0
        assert len(settings.cmp_password.get_secret_value()) > 0
        assert len(settings.imap_username.get_secret_value()) > 0
        assert len(settings.imap_password.get_secret_value()) > 0

    def _minimal_env_content(self) -> str:
        return (
            "CMP_CAS_URL=https://ep.iotcc.telkomsel.com/cas/login\n"
            "CMP_PRODUCTS_URL=https://ep.iotcc.telkomsel.com/#!products\n"
            "CMP_DASHBOARD_URL=https://ep.iotcc.telkomsel.com/#!dashboard\n"
            "CMP_USERNAME=testuser\n"
            "CMP_PASSWORD=testpass\n"
            "IMAP_USERNAME=imapuser\n"
            "IMAP_PASSWORD=imappass\n"
        )

    def test_process_env_overrides_dotenv(self, tmp_path, monkeypatch):
        """Process environment variables must override .env values."""
        dot_env = tmp_path / ".env"
        dot_env.write_text(self._minimal_env_content() + "LOG_LEVEL=INFO\n")

        # Ensure no stale LOG_LEVEL in the process environment.
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        settings = load_settings(env_file=dot_env)
        assert settings.log_level == "INFO"

        # Now set LOG_LEVEL=DEBUG in the process environment: it must win.
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        settings = load_settings(env_file=dot_env)
        assert settings.log_level == "DEBUG"

    def test_explicit_env_mapping_highest_precedence(self, tmp_path, monkeypatch):
        """Explicit env mapping overrides both .env and process environment."""
        dot_env = tmp_path / ".env"
        dot_env.write_text(self._minimal_env_content() + "LOG_LEVEL=INFO\n")

        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        settings = load_settings(env_file=dot_env, env={"LOG_LEVEL": "DEBUG"})
        assert settings.log_level == "DEBUG"

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
    def test_tls_verification_can_be_disabled(self):
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
        settings = load_settings(env=env)
        assert settings.imap_verify_tls is False

    def test_tls_verification_defaults_to_true(self):
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
        assert settings.imap_verify_tls is True


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

    def test_firefox_executable_path_unset(self):
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
        assert settings.firefox_executable_path is None

    def test_firefox_executable_path_accepts_existing_external_file(self, tmp_path):
        executable = tmp_path / "firefox.exe"
        executable.write_text("test executable")
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "FIREFOX_EXECUTABLE_PATH": str(executable),
        }
        settings = load_settings(env=env)
        assert settings.firefox_executable_path == executable.resolve()

    @pytest.mark.parametrize(
        "path_value, error",
        [
            ("firefox.exe", "absolute path"),
            ("C:/missing/firefox.exe", "existing regular file"),
        ],
    )
    def test_firefox_executable_path_rejects_invalid_path(self, path_value, error):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "FIREFOX_EXECUTABLE_PATH": path_value,
        }
        with pytest.raises(ConfigError, match=error):
            load_settings(env=env)

    def test_firefox_executable_path_rejects_empty_value(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "FIREFOX_EXECUTABLE_PATH": "   ",
        }
        settings = load_settings(env=env)
        assert settings.firefox_executable_path is None

    def test_firefox_executable_path_rejects_directory(self, tmp_path):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "FIREFOX_EXECUTABLE_PATH": str(tmp_path),
        }
        with pytest.raises(ConfigError, match="existing regular file"):
            load_settings(env=env)

    def test_firefox_executable_path_rejects_repository_file(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "FIREFOX_EXECUTABLE_PATH": str(PROJECT_ROOT / "config.py"),
        }
        with pytest.raises(ConfigError, match="outside the repository workspace"):
            load_settings(env=env)


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



class TestCheckpointConfig:
    def test_default_checkpoint_auth_mode(self):
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
        assert settings.checkpoint_auth_mode == "client_managed"
        assert settings.checkpoint_allow_interactive is False
        assert settings.checkpoint_username is None
        assert settings.checkpoint_password is None

    def test_checkpoint_credentials_mode_valid(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "credentials",
            "CHECKPOINT_USERNAME": "cp_user",
            "CHECKPOINT_PASSWORD": "cp_password",
        }
        settings = load_settings(env=env)
        assert settings.checkpoint_auth_mode == "credentials"
        assert settings.checkpoint_allow_interactive is False
        assert isinstance(settings.checkpoint_username, SecretValue)
        assert isinstance(settings.checkpoint_password, SecretValue)
        assert settings.checkpoint_username.get_secret_value() == "cp_user"
        assert settings.checkpoint_password.get_secret_value() == "cp_password"
        assert str(settings.checkpoint_username) == "***REDACTED***"
        assert repr(settings.checkpoint_username) == "SecretValue(***REDACTED***)"
        assert str(settings.checkpoint_password) == "***REDACTED***"
        assert repr(settings.checkpoint_password) == "SecretValue(***REDACTED***)"

    def test_checkpoint_credentials_mode_missing_username(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "credentials",
            "CHECKPOINT_PASSWORD": "cp_password",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_USERNAME"):
            load_settings(env=env)

    def test_checkpoint_credentials_mode_blank_username(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "credentials",
            "CHECKPOINT_USERNAME": "   ",
            "CHECKPOINT_PASSWORD": "cp_password",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_USERNAME"):
            load_settings(env=env)

    def test_checkpoint_credentials_mode_missing_password(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "credentials",
            "CHECKPOINT_USERNAME": "cp_user",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_PASSWORD"):
            load_settings(env=env)

    def test_checkpoint_credentials_mode_blank_password(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "credentials",
            "CHECKPOINT_USERNAME": "cp_user",
            "CHECKPOINT_PASSWORD": "   ",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_PASSWORD"):
            load_settings(env=env)

    def test_checkpoint_auth_mode_invalid(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "invalid_mode",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_AUTH_MODE"):
            load_settings(env=env)

    def test_checkpoint_interactive_mode_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_ALLOW_INTERACTIVE": "true",
        }
        with pytest.raises(ConfigError, match="CHECKPOINT_ALLOW_INTERACTIVE"):
            load_settings(env=env)

    def test_checkpoint_credentials_ignored_in_client_managed(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "CHECKPOINT_AUTH_MODE": "client_managed",
            "CHECKPOINT_USERNAME": "cp_user",
            "CHECKPOINT_PASSWORD": "cp_pass",
        }
        settings = load_settings(env=env)
        assert settings.checkpoint_auth_mode == "client_managed"
        assert settings.checkpoint_username is None
        assert settings.checkpoint_password is None


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
            "IMAP_HOST": "  mail.company.local  ",
        }
        settings = load_settings(env=env)
        assert settings.imap_host == "mail.company.local"

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


class TestPageZoomPercentValidation:
    """Tests for PAGE_ZOOM_PERCENT configuration."""

    def test_default_page_zoom_percent_is_67(self):
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
        assert settings.page_zoom_percent == 67

    def test_custom_page_zoom_percent_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "PAGE_ZOOM_PERCENT": "75",
        }
        settings = load_settings(env=env)
        assert settings.page_zoom_percent == 75

    def test_out_of_range_page_zoom_percent_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "PAGE_ZOOM_PERCENT": "10",
        }
        with pytest.raises(ConfigError, match="PAGE_ZOOM_PERCENT must be between 25 and 200"):
            load_settings(env=env)


class TestWarpModeValidation:
    """Tests for WARP_MODE and WARP_PROXY_PORT configuration."""

    def test_default_warp_mode_is_proxy_and_port_is_40000(self):
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
        assert settings.warp_mode == "proxy"
        assert settings.warp_proxy_port == 40000

    def test_legacy_warp_mode_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "WARP_MODE": "warp",
        }
        settings = load_settings(env=env)
        assert settings.warp_mode == "warp"

    def test_custom_warp_proxy_port_accepted(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "WARP_PROXY_PORT": "10800",
        }
        settings = load_settings(env=env)
        assert settings.warp_proxy_port == 10800

    def test_invalid_warp_mode_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "WARP_MODE": "doh",
        }
        with pytest.raises(ConfigError, match="WARP_MODE must be warp or proxy"):
            load_settings(env=env)

    def test_invalid_warp_proxy_port_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "WARP_PROXY_PORT": "80",
        }
        with pytest.raises(ConfigError, match="WARP_PROXY_PORT must be between 1024 and 65535"):
            load_settings(env=env)


class TestLowMemoryModeValidation:
    """Tests for LOW_MEMORY_MODE configuration."""

    def test_low_memory_mode_default_true(self):
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
        assert settings.low_memory_mode is True

    def test_low_memory_mode_false_values_accepted(self):
        for val in ["false", "0", "off", "no", "False", "OFF", "NO"]:
            env = {
                "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
                "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
                "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
                "CMP_USERNAME": "testuser",
                "CMP_PASSWORD": "testpass",
                "IMAP_USERNAME": "imapuser",
                "IMAP_PASSWORD": "imappass",
                "LOW_MEMORY_MODE": val,
            }
            settings = load_settings(env=env)
            assert settings.low_memory_mode is False

    def test_low_memory_mode_true_values_accepted(self):
        for val in ["true", "1", "on", "yes", "True", "ON", "YES"]:
            env = {
                "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
                "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
                "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
                "CMP_USERNAME": "testuser",
                "CMP_PASSWORD": "testpass",
                "IMAP_USERNAME": "imapuser",
                "IMAP_PASSWORD": "imappass",
                "LOW_MEMORY_MODE": val,
            }
            settings = load_settings(env=env)
            assert settings.low_memory_mode is True

    def test_low_memory_mode_invalid_rejected(self):
        env = {
            "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
            "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
            "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
            "CMP_USERNAME": "testuser",
            "CMP_PASSWORD": "testpass",
            "IMAP_USERNAME": "imapuser",
            "IMAP_PASSWORD": "imappass",
            "LOW_MEMORY_MODE": "invalid_mode",
        }
        with pytest.raises(ConfigError, match="LOW_MEMORY_MODE must be a boolean value"):
            load_settings(env=env)
