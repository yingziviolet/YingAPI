import importlib.util
import socket
import sys
import uuid
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "ying_launcher_test",
    Path(__file__).parents[1] / "packaging" / "launcher.py",
)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_data_dir_reuses_legacy_profile(monkeypatch, tmp_path):
    legacy = tmp_path / "LLMGateway"
    legacy.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.data_dir() == legacy
    assert not (tmp_path / "Ying").exists()


def test_pick_port_skips_busy_preferred_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert launcher.pick_port(port) != port


def test_window_failure_opens_browser_before_tray(monkeypatch):
    opened = []
    messages = []
    tray = []
    config = {"url": "http://127.0.0.1:8080/console/", "log": "test.log"}
    server_thread = object()

    monkeypatch.delenv("GW_NO_BROWSER", raising=False)
    monkeypatch.setattr(launcher, "webview2_available", lambda: True)
    monkeypatch.setattr(launcher, "run_window", lambda _config: False)
    monkeypatch.setattr(launcher, "show_message", lambda *args, **kwargs: messages.append((args, kwargs)))
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher, "run_tray", lambda c, s: tray.append((c, s)))

    launcher.run_ui(config, server_thread)

    assert opened == [config["url"]]
    assert len(messages) == 1
    assert tray == [(config, server_thread)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_single_instance_mutex():
    name = rf"Local\Ying-test-{uuid.uuid4().hex}"
    first = launcher.acquire_single_instance(name)
    try:
        assert first
        assert launcher.acquire_single_instance(name) is None
    finally:
        launcher.release_single_instance(first)
