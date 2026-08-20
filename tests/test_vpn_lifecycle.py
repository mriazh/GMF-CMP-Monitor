"""Offline lifecycle tests for the VPN controller."""

from __future__ import annotations

from config import load_settings
from vpn import ConnectivityController


def make_settings(overrides: dict[str, str] | None = None):
    env = {
        "CMP_CAS_URL": "https://ep.iotcc.telkomsel.com/cas/login",
        "CMP_PRODUCTS_URL": "https://ep.iotcc.telkomsel.com/#!products",
        "CMP_DASHBOARD_URL": "https://ep.iotcc.telkomsel.com/#!dashboard",
        "CMP_USERNAME": "u",
        "CMP_PASSWORD": "p",
        "IMAP_USERNAME": "i",
        "IMAP_PASSWORD": "p",
        "HEADLESS": "false",
        "CHECKPOINT_TRAC_PATH": "trac",
        "WARP_CLI_PATH": "warp-cli",
        "WARP_MODE": "warp",
    }
    if overrides:
        env.update(overrides)
    return load_settings(env=env)


def test_finish_authentication_disconnects_checkpoint_before_warp():
    events: list[str] = []

    class Checkpoint:
        def disconnect(self):
            events.append("checkpoint-disconnect")

    class Warp:
        def connect(self):
            events.append("warp-connect")

        def validate(self):
            events.append("warp-validate")

    controller = ConnectivityController(make_settings(), object(), Checkpoint(), Warp())
    controller.finish_authentication()

    assert events == ["checkpoint-disconnect", "warp-connect", "warp-validate"]


def test_restart_auth_connectivity_only_prepares_checkpoint_when_probe_is_unreachable():
    events: list[str] = []

    class Probe:
        def is_reachable(self):
            events.append("probe")
            return True

    class Checkpoint:
        def connect(self):
            events.append("checkpoint-connect")

        def wait_until_connected(self):
            events.append("checkpoint-wait")

    controller = ConnectivityController(make_settings(), Probe(), Checkpoint(), object())
    controller.restart_auth_connectivity()

    assert events == ["probe"]


def test_prepare_reauthentication_settles_and_retries_probe_after_owned_warp_disconnect():
    events: list[str] = []

    probe_results = [False, True]

    class Probe:
        def is_reachable(self):
            res = probe_results.pop(0) if probe_results else True
            events.append(f"probe-{res}")
            return res

    class Checkpoint:
        def connect(self):
            events.append("checkpoint-connect")

        def wait_until_connected(self):
            events.append("checkpoint-wait")

    class OwnedWarp:
        owned = True

        def disconnect(self):
            events.append("warp-disconnect")
            self.owned = False

    sleeps: list[float] = []

    def fake_sleep(seconds: float):
        sleeps.append(seconds)

    controller = ConnectivityController(
        make_settings(),
        Probe(),
        Checkpoint(),
        OwnedWarp(),
        sleeper=fake_sleep,
    )
    controller.prepare_reauthentication()

    assert events == ["warp-disconnect", "probe-False", "probe-True"]
    assert len(sleeps) >= 2
    assert "checkpoint-connect" not in events


def test_prepare_reauthentication_leaves_warp_connected_in_proxy_mode():
    events: list[str] = []

    class Probe:
        def is_reachable(self):
            events.append("probe")
            return True

    class Checkpoint:
        def connect(self):
            events.append("checkpoint-connect")

    class OwnedWarp:
        owned = True

        def disconnect(self):
            events.append("warp-disconnect")
            self.owned = False

    controller = ConnectivityController(
        make_settings({"WARP_MODE": "proxy"}),
        Probe(),
        Checkpoint(),
        OwnedWarp(),
    )
    controller.prepare_reauthentication()

    assert events == ["probe"]
    assert "warp-disconnect" not in events
