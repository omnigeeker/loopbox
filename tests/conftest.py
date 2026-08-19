"""Shared pytest fixtures for the loopbox test suite.

Every test that touches on-disk state gets its own ``LOOPBOX_HOME`` under a
fresh ``tmp_path`` via the ``loopbox_home`` fixture, so tests never touch the
real ``~/.loopbox``.
"""

from __future__ import annotations

import json
import shutil

import pytest

from loopbox.store import ENV_HOME


@pytest.fixture()
def loopbox_home(tmp_path, monkeypatch):
    """Point LOOPBOX_HOME at a fresh per-test tmp dir."""
    monkeypatch.setenv(ENV_HOME, str(tmp_path))
    return tmp_path


def registry_path(root):
    """Return the registry file path under a LOOPBOX_HOME root."""
    return root / "sandboxes.json"


def purge_sandbox(root, sandbox_id):
    """Delete every on-disk artifact of a sandbox (mirrors CLI rm --purge)."""
    shutil.rmtree(root / "sandboxes" / sandbox_id, ignore_errors=True)
    shutil.rmtree(root / "snapshots" / sandbox_id, ignore_errors=True)
    shutil.rmtree(root / "vms" / sandbox_id, ignore_errors=True)


def all_sandbox_ids(root):
    """Return every sandbox id present in the registry or on disk."""
    ids = set()
    reg = registry_path(root)
    if reg.exists():
        ids.update(json.loads(reg.read_text(encoding="utf-8")))
    sandboxes = root / "sandboxes"
    if sandboxes.exists():
        ids.update(e.name for e in sandboxes.iterdir())
    return ids


@pytest.fixture()
def tracked_home(loopbox_home):
    """LOOPBOX_HOME plus best-effort kill/cleanup of every created sandbox.

    Keeps spawned process groups (e.g. test ``sleep`` commands) from leaking
    onto the machine when a test fails mid-way.
    """
    yield loopbox_home
    from loopbox.backends import get_backend
    from loopbox.store import Store

    store = Store()
    for sid in all_sandbox_ids(loopbox_home):
        try:
            record = store.get(sid)
        except Exception:
            record = {"id": sid, "backend": "seatbelt", "engine": {}}
        try:
            get_backend(record.get("backend") or "seatbelt").kill(record)
        except Exception:
            pass
        try:
            store.remove(sid)
        except Exception:
            pass


@pytest.fixture()
def no_harness(monkeypatch):
    """Force the loop engine's rule-based thinking fallback.

    ``LOOPBOX_HARNESS=" "`` disables the env override, and patching
    ``engine._find_harness`` keeps tests hermetic on machines where a real
    ``codex``/``claude`` CLI is installed on PATH.
    """
    from loopbox.loop import engine

    monkeypatch.setenv("LOOPBOX_HARNESS", " ")
    monkeypatch.setattr(engine, "_find_harness", lambda: None)
