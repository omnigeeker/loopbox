"""Tests for the harness registry and runner (loopbox.harness)."""

from __future__ import annotations

import os
import shutil
import stat

import pytest

from loopbox import harness
from loopbox.sdk import Sandbox

pytestmark = pytest.mark.skipif(
    shutil.which("sandbox-exec") is None,
    reason="requires macOS sandbox-exec",
)


def test_list_available_structure():
    entries = harness.list_available()
    names = [e["name"] for e in entries]
    assert names == ["codex", "kimi", "claude", "dsh"]
    for entry in entries:
        assert set(entry) == {"name", "binary", "installed", "path", "notes"}
        assert entry["binary"] == entry["name"]
        assert isinstance(entry["installed"], bool)
        assert entry["notes"]
        if entry["installed"]:
            assert os.path.exists(entry["path"])
        else:
            assert entry["path"] is None


def test_describe_known_structure():
    info = harness.describe("codex")
    for key in (
        "name",
        "binary",
        "known",
        "installed",
        "path",
        "version",
        "version_probe",
        "launch_examples",
        "notes",
        "install_hint",
    ):
        assert key in info
    assert info["known"] is True
    assert info["version_probe"] == ["codex", "--version"]


def test_describe_unknown_falls_back_to_custom_adapter():
    info = harness.describe("totally-made-up")
    assert info["known"] is False
    assert info["installed"] is False
    assert info["binary"] == "totally-made-up"
    assert "opaque" in info["notes"]


@pytest.fixture()
def fake_harness(tmp_path, monkeypatch):
    """A fake harness binary on PATH that echoes its argv and --version."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    script = bin_dir / "fakeagent"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "fakeagent 1.0"; exit 0; fi\n'
        'echo "fake-agent-ran:$*"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return script


def test_custom_binary_run_inside_sandbox(tracked_home, fake_harness):
    sbx = Sandbox.create()
    try:
        result = harness.run(sbx.id, "fakeagent", ["--flag", "value"])
        assert result.ok
        assert result.stdout.strip() == "fake-agent-ran:--flag value"
    finally:
        sbx.kill()


def test_custom_binary_version_probe(tracked_home, fake_harness):
    info = harness.describe("fakeagent")
    assert info["installed"] is True
    assert info["version"] == "fakeagent 1.0"


def test_missing_custom_binary_raises(tracked_home):
    sbx = Sandbox.create()
    try:
        with pytest.raises(FileNotFoundError, match="not on PATH"):
            harness.run(sbx.id, "fakeagent-not-present", [])
    finally:
        sbx.kill()


def test_run_unknown_sandbox_raises(tracked_home, fake_harness):
    with pytest.raises(Exception, match="not found"):
        harness.run("sbx_missing", "fakeagent", [])


def test_build_argv_passthrough():
    spec = harness._spec("codex")
    assert spec.build_argv(["exec", "hi"]) == ["codex", "exec", "hi"]
    custom = harness._spec("myrunner")
    assert custom.build_argv([]) == ["myrunner"]


def test_harness_main_list(capsys):
    rc = harness.harness_main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("codex", "kimi", "claude", "dsh"):
        assert name in out


def test_harness_main_run_sandbox(tracked_home, fake_harness, capsys):
    sbx = Sandbox.create()
    try:
        rc = harness.harness_main(["run", sbx.id, "fakeagent", "--", "--work", "now"])
    finally:
        sbx.kill()
    assert rc == 0
    assert "fake-agent-ran:--work now" in capsys.readouterr().out
