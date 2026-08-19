"""Unit tests for the vz backend's helper discovery (no VM is ever booted).

``_find_vzrunner`` historically mixed ``str`` and ``Path`` candidates; these
tests pin down the type handling without requiring the Swift helper.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from loopbox.backends import get_backend, backend_names
from loopbox.backends.vz import VzBackend, _find_vzrunner


def test_vz_backend_registered():
    assert "vz" in backend_names()
    backend = get_backend("vz")
    assert isinstance(backend, VzBackend)
    assert backend.name == "vz"


def test_find_vzrunner_prefers_path_on_path(tmp_path, monkeypatch):
    fake = tmp_path / "vzrunner"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))
    found = _find_vzrunner()
    assert isinstance(found, Path)
    assert found == fake


def test_find_vzrunner_handles_str_candidate(tmp_path, monkeypatch):
    """shutil.which returns ``str | None``; the mix must not break discovery."""
    fake = tmp_path / "vzrunner-str"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))
    backend = VzBackend()
    assert backend._runner() == fake


def test_find_vzrunner_missing_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # Also point the in-repo fallback at an empty tree.
    fake_root = tmp_path / "pkg"
    module_file = fake_root / "backends" / "vz.py"
    module_file.parent.mkdir(parents=True)
    monkeypatch.setattr("loopbox.backends.vz.__file__", str(module_file))
    with pytest.raises(FileNotFoundError, match="build.sh"):
        _find_vzrunner()


def test_get_backend_unknown_raises():
    from loopbox.sdk import SandboxError
    from loopbox.sdk import Sandbox

    with pytest.raises(ValueError, match="unknown backend"):
        get_backend("nope")
    with pytest.raises(SandboxError, match="unknown template"):
        Sandbox._backend_for("nope")
