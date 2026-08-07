"""Offline tests for Check Point and WARP adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import load_settings
from vpn import (
    CheckPointClient,
    CheckPointError,
    ConnectivityController,
    WarpClient,
    WarpError,
    WarpValidationError,
    parse_checkpoint_status,
    parse_warp_status,
)

BASE_ENV = {
    "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
    "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
    "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
    "CMP_USERNAME": "cmp-user",
    "CMP_PASSWORD": "cmp-pass",
    "IMAP_USERNAME": "imap-user",
    "IMAP_PASSWORD": "imap-pass",
}


def settings(**overrides):
    values = {
        **BASE_ENV,
        "HEADLESS": "false",
        "CHECKPOINT_TRAC_PATH": "trac",
        "WARP_CLI_PATH": "warp-cli",
        **overrides,
    }
    return load_settings(env=values)


def test_checkpoint_info_parses_connected_site():
    output = """Conn VPN-GMF:
 Gw: 118.97.70.226
 Status: Connected
 Active site: true
 Gateway list:
 (Connected) GMFINETFW01
"""
    parsed = parse_checkpoint_status(output, "VPN-GMF")
    assert parsed.connected is True


def test_checkpoint_commands_client_managed_never_include_credentials():
    client = CheckPointClient(settings(CHECKPOINT_TRAC_PATH="trac"))
    assert client.command_connect() == [
        "trac",
        "connect",
        "-s",
        "VPN-GMF",
        "-g",
        "GMFINETFW01",
    ]
    assert "cmp-pass" not in client.command_connect()
    assert client.command_disconnect() == ["trac", "disconnect", "-g", "GMFINETFW01"]


def test_checkpoint_commands_credentials_mode_includes_username_and_password():
    client = CheckPointClient(
        settings(
            CHECKPOINT_TRAC_PATH="trac",
            CHECKPOINT_AUTH_MODE="credentials",
            CHECKPOINT_USERNAME="my_cp_user",
            CHECKPOINT_PASSWORD="my_cp_secret_password",
        )
    )
    assert client.command_connect() == [
        "trac",
        "connect",
        "-s",
        "VPN-GMF",
        "-u",
        "my_cp_user",
        "-p",
        "my_cp_secret_password",
        "-g",
        "GMFINETFW01",
    ]
    assert client.command_disconnect() == ["trac", "disconnect", "-g", "GMFINETFW01"]


def test_checkpoint_connect_credentials_mode_calls_connect_with_args():
    calls: list[list[str]] = []
    info = ["Conn VPN-GMF:\n Status: Idle\n", "Conn VPN-GMF:\n Status: Connected\n"]

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "info":
            return SimpleNamespace(returncode=0, stdout=info.pop(0), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    client = CheckPointClient(
        settings(
            CHECKPOINT_TRAC_PATH="trac",
            CHECKPOINT_AUTH_MODE="credentials",
            CHECKPOINT_USERNAME="my_cp_user",
            CHECKPOINT_PASSWORD="my_cp_password",
        ),
        runner=runner,
    )
    client.connect()
    assert client.owned is True
    assert calls[0][1:] == ["info", "-s", "VPN-GMF"]
    assert calls[1][1:] == [
        "connect",
        "-s",
        "VPN-GMF",
        "-u",
        "my_cp_user",
        "-p",
        "my_cp_password",
        "-g",
        "GMFINETFW01",
    ]


def test_checkpoint_connect_marks_only_new_connection_owned():
    calls: list[list[str]] = []
    info = ["Conn VPN-GMF:\n Status: Idle\n", "Conn VPN-GMF:\n Status: Connected\n"]

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "info":
            return SimpleNamespace(returncode=0, stdout=info.pop(0), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    client = CheckPointClient(settings(CHECKPOINT_TRAC_PATH="trac"), runner=runner)
    client.connect()
    assert client.owned is True
    assert calls[0][1:] == ["info", "-s", "VPN-GMF"]
    assert calls[1][1:] == ["connect", "-s", "VPN-GMF", "-g", "GMFINETFW01"]


def test_checkpoint_preexisting_connection_is_reused_without_connect_or_ownership():
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="Conn VPN-GMF:\n Status: Connected\n",
            stderr="",
        )

    client = CheckPointClient(settings(CHECKPOINT_TRAC_PATH="trac"), runner=runner)
    client.connect()

    assert client.owned is False
    assert [command[1] for command in calls] == ["info"]
    client.disconnect()
    assert [command[1] for command in calls] == ["info"]


def test_checkpoint_cleanup_does_not_disconnect_preexisting_connection():
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="Conn VPN-GMF:\n Status: Connected\n",
            stderr="",
        )

    client = CheckPointClient(settings(CHECKPOINT_TRAC_PATH="trac"), runner=runner)
    assert client.status().connected is True
    client.disconnect()
    assert not any(command[1] == "disconnect" for command in calls)


def test_warp_status_parses_connection_and_mode():
    parsed = parse_warp_status("Status update: Connected\nMode: Warp\n")
    assert parsed.connected is True
    assert parsed.mode == "warp"


def test_warp_status_parses_json():
    parsed = parse_warp_status('{"status":"Connected","mode":"Warp"}')
    assert parsed == parse_warp_status("Status update: Connected\nMode: Warp\n")


def test_warp_rejects_connected_unknown_mode():
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="Status update: Connected\n",
            stderr="",
        )

    with pytest.raises(WarpValidationError, match="mode"):
        WarpClient(settings(), runner=runner).connect()


def test_warp_rejects_connected_wrong_mode():
    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="Status update: Connected\nMode: doh\n",
            stderr="",
        )

    with pytest.raises(WarpValidationError):
        WarpClient(settings(), runner=runner).connect()


def test_warp_connect_and_disconnect_are_owned():
    calls: list[list[str]] = []
    status_outputs = [
        "Status update: Disconnected\n",
        "Status update: Connected\nMode: Warp\n",
        "Status update: Connected\nMode: Warp\n",
    ]

    def runner(command, **kwargs):
        calls.append(command)
        if command[-1] == "status":
            return SimpleNamespace(returncode=0, stdout=status_outputs.pop(0), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    client = WarpClient(
        settings(
            WARP_CLI_PATH="warp-cli",
            WARP_CONNECT_TIMEOUT_SECONDS="1",
            WARP_STATUS_POLL_INTERVAL_SECONDS="0.1",
        ),
        runner=runner,
        sleeper=lambda _: None,
    )
    client.connect()
    assert client.owned is True
    client.wait_until_connected()
    client.disconnect()
    assert calls[0][1:] == ["--no-ansi", "status"]
    assert calls[1][1:] == ["connect"]
    assert calls[2][1:] == ["--no-ansi", "status"]
    assert calls[3][1:] == ["disconnect"]


def test_warp_existing_compliant_connection_is_reused_without_disconnect():
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="Status update: Connected\nMode: Warp\n",
            stderr="",
        )

    client = WarpClient(settings(WARP_CLI_PATH="warp-cli"), runner=runner)
    client.connect()

    assert client.owned is False
    assert [command[1:] for command in calls] == [["--no-ansi", "status"]]
    client.disconnect()
    assert [command[1:] for command in calls] == [["--no-ansi", "status"]]


def test_warp_trace_requires_warp_on_and_does_not_log_body(caplog):
    class Response:
        status = 200

        def read(self):
            return b"ip=203.0.113.10\nwarp=on\n" 

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="Status update: Connected\nMode: Warp\n",
            stderr="",
        )

    client = WarpClient(settings(), runner=runner, opener=lambda request, timeout: Response())
    client.validate()
    assert "203.0.113.10" not in caplog.text


def test_warp_trace_rejects_missing_confirmation():
    class Response:
        status = 200

        def read(self):
            return b"warp=off\n"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="Status update: Connected\nMode: Warp\n",
            stderr="",
        )

    with pytest.raises(WarpValidationError):
        WarpClient(settings(), runner=runner, opener=lambda request, timeout: Response()).validate()


def test_config_loads_vpn_fields_and_quoted_paths(tmp_path):
    trac = tmp_path / "trac.exe"
    warp = tmp_path / "warp-cli.exe"
    env = {
        **BASE_ENV,
        "CHECKPOINT_TRAC_PATH": f'"{trac}"',
        "CHECKPOINT_GATEWAY_IP": "118.97.70.226",
        "WARP_CLI_PATH": f'"{warp}"',
        "OFFICE_NETWORK_PROBE_SECONDARY_URL": "https://soe.gmf-aeroasia.co.id/",
    }
    loaded = load_settings(env=env)
    assert loaded.checkpoint_trac_path == trac.resolve()
    assert loaded.checkpoint_site == "VPN-GMF"
    assert loaded.checkpoint_gateway_name == "GMFINETFW01"
    assert loaded.checkpoint_gateway_ip == "118.97.70.226"
    assert loaded.warp_cli_path == warp.resolve()
    assert loaded.warp_mode == "warp"
    assert loaded.office_network_probe_port == 993


def test_config_rejects_dns_only_warp():
    with pytest.raises(Exception, match="WARP_MODE"):
        load_settings(env={**BASE_ENV, "WARP_MODE": "doh"})


def test_controller_uses_probe_before_checkpoint_and_orders_cleanup():
    events: list[str] = []

    class Probe:
        def __init__(self):
            self.results = iter([False, True])

        def is_reachable(self):
            events.append("probe")
            return next(self.results)

    class Checkpoint:
        owned = False

        def connect(self):
            events.append("checkpoint-connect")
            self.owned = True

        def wait_until_connected(self):
            events.append("checkpoint-wait")

        def disconnect(self):
            events.append("checkpoint-disconnect")
            self.owned = False

    class Warp:
        owned = False

        def connect(self):
            events.append("warp-connect")
            self.owned = True

        def validate(self):
            events.append("warp-validate")

        def disconnect(self):
            events.append("warp-disconnect")
            self.owned = False

    controller = ConnectivityController(settings(), Probe(), Checkpoint(), Warp())
    controller.ensure_imap_reachable()
    controller.disconnect_checkpoint_after_imap()
    controller.prepare_warp()
    controller.cleanup()
    assert events == [
        "probe",
        "checkpoint-connect",
        "checkpoint-wait",
        "probe",
        "checkpoint-disconnect",
        "warp-connect",
        "warp-validate",
        "checkpoint-disconnect",
        "warp-disconnect",
    ]


def test_controller_cleanup_continues_to_warp_when_checkpoint_cleanup_fails():
    events: list[str] = []

    class Checkpoint:
        owned = True

        def disconnect(self):
            events.append("checkpoint-disconnect")
            raise CheckPointError("failed")

    class Warp:
        owned = True

        def disconnect(self):
            events.append("warp-disconnect")

    controller = ConnectivityController(settings(), object(), Checkpoint(), Warp())
    controller.cleanup()

    assert events == ["checkpoint-disconnect", "warp-disconnect"]


def test_checkpoint_connect_retries_and_is_bounded():
    calls: list[list[str]] = []
    attempts = 0
    checkpoint_settings = settings(
        CHECKPOINT_RETRY_LIMIT="2",
        CHECKPOINT_CONNECT_TIMEOUT_SECONDS="1",
        CHECKPOINT_STATUS_POLL_INTERVAL_SECONDS="0.1",
    )

    def runner(command, **kwargs):
        nonlocal attempts
        calls.append(command)
        if command[1] == "info":
            return SimpleNamespace(returncode=0, stdout="Conn VPN-GMF:\n Status: Idle\n", stderr="")
        attempts += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="failure")

    client = CheckPointClient(
        checkpoint_settings,
        runner=runner,
        sleeper=lambda seconds: None,
        monotonic=lambda: 0.0,
    )
    with pytest.raises(CheckPointError):
        client.connect()

    assert attempts == 2
    assert client.owned is False
    assert all(command[1:] != ["connect", "-s", "VPN-GMF", "-g", "GMFINETFW01", "cmp-pass"] for command in calls)


def test_warp_connect_retries_and_is_bounded():
    calls: list[list[str]] = []
    warp_settings = settings(
        WARP_RETRY_LIMIT="2",
        WARP_CONNECT_TIMEOUT_SECONDS="1",
        WARP_STATUS_POLL_INTERVAL_SECONDS="0.1",
    )

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "status":
            return SimpleNamespace(returncode=1, stdout="", stderr="failure")
        return SimpleNamespace(returncode=1, stdout="", stderr="failure")

    client = WarpClient(warp_settings, runner=runner, sleeper=lambda _: None)
    with pytest.raises(WarpError):
        client.connect()

    assert len([command for command in calls if command[1] == "connect"]) == 2
    assert client.owned is False


def test_warp_connect_polls_disconnected_then_connected():
    calls: list[list[str]] = []
    sleeps: list[float] = []
    now = [0.0]
    status_outputs = [
        "Status update: Disconnected\n",
        "Status update: Disconnected\n",
        "Status update: Connected\nMode: Warp\n",
    ]

    def runner(command, **kwargs):
        calls.append(command)
        if command[-1] == "status":
            return SimpleNamespace(returncode=0, stdout=status_outputs.pop(0), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    client = WarpClient(
        settings(
            WARP_CONNECT_TIMEOUT_SECONDS="1",
            WARP_STATUS_POLL_INTERVAL_SECONDS="0.25",
        ),
        runner=runner,
        sleeper=sleep,
        monotonic=lambda: now[0],
    )
    client.connect()

    assert client.owned is True
    assert [command[1:] for command in calls] == [
        ["--no-ansi", "status"],
        ["connect"],
    ]
    assert sleeps == []


def test_warp_connect_timeout_is_bounded():
    calls: list[list[str]] = []
    now = [0.0]

    def runner(command, **kwargs):
        calls.append(command)
        if command[-1] == "status":
            return SimpleNamespace(returncode=0, stdout="Status update: Disconnected\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def sleep(seconds):
        now[0] += seconds

    client = WarpClient(
        settings(
            WARP_CONNECT_TIMEOUT_SECONDS="0.5",
            WARP_STATUS_POLL_INTERVAL_SECONDS="0.2",
        ),
        runner=runner,
        sleeper=sleep,
        monotonic=lambda: now[0],
    )
    client.connect()
    with pytest.raises(WarpError, match="timed out"):
        client.wait_until_connected()

    assert len([command for command in calls if command[-1] == "status"]) == 5


def test_controller_retries_warp_validation_and_disconnects_only_owned():
    events: list[str] = []

    class Warp:
        def __init__(self):
            self.owned = False
            self.attempts = 0

        def connect(self):
            self.attempts += 1
            events.append("warp-connect")
            self.owned = True

        def validate(self):
            events.append("warp-validate")
            if self.attempts == 1:
                raise WarpValidationError("trace failed")

        def disconnect(self):
            events.append("warp-disconnect")
            self.owned = False

    controller = ConnectivityController(
        settings(WARP_RETRY_LIMIT="2", WARP_STATUS_POLL_INTERVAL_SECONDS="0.1"),
        object(),
        object(),
        Warp(),
        sleeper=lambda _: None,
    )
    controller.prepare_warp()

    assert events == [
        "warp-connect",
        "warp-validate",
        "warp-disconnect",
        "warp-connect",
        "warp-validate",
    ]


def test_checkpoint_retries_are_bounded_and_cleanup_owned_connection():
    events: list[str] = []

    class Probe:
        def is_reachable(self):
            events.append("probe")
            return False

    class Checkpoint:
        def __init__(self):
            self.owned = False

        def connect(self):
            events.append("checkpoint-connect")
            self.owned = True

        def wait_until_connected(self):
            events.append("checkpoint-wait")

        def disconnect(self):
            events.append("checkpoint-disconnect")
            self.owned = False

    checkpoint = Checkpoint()
    controller = ConnectivityController(
        settings(CHECKPOINT_RETRY_LIMIT="2", CHECKPOINT_STATUS_POLL_INTERVAL_SECONDS="0.1"),
        Probe(),
        checkpoint,
        object(),
        sleeper=lambda _: None,
    )
    with pytest.raises(CheckPointError):
        controller.ensure_imap_reachable()

    assert events == [
        "probe",
        "checkpoint-connect",
        "checkpoint-wait",
        "probe",
        "checkpoint-disconnect",
        "checkpoint-connect",
        "checkpoint-wait",
        "probe",
        "checkpoint-disconnect",
    ]


def test_connectivity_controller_close_delegates_to_cleanup():
    events: list[str] = []

    class Checkpoint:
        def disconnect(self):
            events.append("checkpoint-disconnect")

    class Warp:
        def disconnect(self):
            events.append("warp-disconnect")

    controller = ConnectivityController(settings(), object(), Checkpoint(), Warp())
    controller.close()
    assert events == ["checkpoint-disconnect", "warp-disconnect"]


def test_connectivity_controller_ensure_imap_reachable_skips_checkpoint_when_probe_succeeds():
    events: list[str] = []

    class Probe:
        def is_reachable(self):
            events.append("probe")
            return True

    class Checkpoint:
        def connect(self):
            events.append("checkpoint-connect")

    controller = ConnectivityController(settings(), Probe(), Checkpoint(), object())
    controller.ensure_imap_reachable()
    assert events == ["probe"]


def test_warp_status_queries_mode_from_settings_when_status_lacks_mode():
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[-1] == "status":
            return SimpleNamespace(returncode=0, stdout="Status update: Connected\n", stderr="")
        if command[-1] == "settings":
            return SimpleNamespace(returncode=0, stdout="(default)\tMode: Warp\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    client = WarpClient(settings(WARP_CLI_PATH="warp-cli"), runner=runner)
    st = client.status()
    assert st.connected is True
    assert st.mode == "warp"
    assert len(calls) == 2
    assert calls[0][1:] == ["--no-ansi", "status"]
    assert calls[1][1:] == ["--no-ansi", "settings"]


