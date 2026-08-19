"""Unit tests for Seatbelt profile generation."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from loopbox.backends.seatbelt import SENSITIVE_DENY_READ, build_profile


def test_profile_denies_default_and_grants_workspace(tmp_path):
    ws = tmp_path / "ws"
    profile = build_profile(ws)
    assert "(deny default)" in profile
    assert f'(allow file-read* file-write* (subpath "{ws}"))' in profile


def test_sensitive_paths_always_denied(tmp_path):
    profile = build_profile(tmp_path / "ws")
    for path in SENSITIVE_DENY_READ:
        expanded = str(Path(path).expanduser())
        assert f'(deny file-read* file-write* (subpath "{expanded}"))' in profile


def test_network_modes(tmp_path):
    assert "(allow network-outbound)" in build_profile(tmp_path, network="outbound")
    assert "(allow network*)" in build_profile(tmp_path, network="all")
    denied = build_profile(tmp_path, network="deny")
    assert "(deny network*)" in denied
    assert "(allow network" not in denied


def test_unknown_network_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_profile(tmp_path, network="sometimes")


def test_extra_rw_paths(tmp_path):
    profile = build_profile(tmp_path / "ws", extra_rw=["~/build-cache"])
    assert f'(subpath "{Path("~/build-cache").expanduser()}")' in profile


@pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="requires sandbox-exec")
def test_generated_profile_loads_in_sandbox_exec(tmp_path):
    """Regression: target filters like ``(pname ...)`` are unbound on macOS
    14+ and make sandbox-exec reject the whole profile, breaking everything."""
    profile_path = tmp_path / "profile.sb"
    profile_path.write_text(build_profile(tmp_path / "ws"), encoding="utf-8")
    proc = subprocess.run(
        ["sandbox-exec", "-f", str(profile_path), "/bin/echo", "profile-ok"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "profile-ok"
