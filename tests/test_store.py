"""Unit tests for the registry store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopbox import store as store_mod
from loopbox.store import Store, StoreError


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(store_mod.ENV_HOME, str(tmp_path))
    return Store(root=tmp_path)


def test_add_get_update_remove(store):
    rec = store.add({"id": "sbx_a", "status": "running"})
    assert rec["created_at"] > 0
    assert store.get("sbx_a")["status"] == "running"

    store.update("sbx_a", status="paused")
    assert store.get("sbx_a")["status"] == "paused"

    store.remove("sbx_a")
    with pytest.raises(StoreError):
        store.get("sbx_a")


def test_duplicate_id_rejected(store):
    store.add({"id": "sbx_dup"})
    with pytest.raises(StoreError):
        store.add({"id": "sbx_dup"})


def test_list_sorted_by_created(store):
    store.add({"id": "sbx_1", "created_at": 2})
    store.add({"id": "sbx_2", "created_at": 1})
    assert [r["id"] for r in store.list()] == ["sbx_2", "sbx_1"]


def test_registry_is_valid_json_after_each_write(store):
    store.add({"id": "sbx_json"})
    data = json.loads(Path(store.path).read_text())
    assert "sbx_json" in data


def test_roundtrip_preserves_nested_records(store):
    record = {
        "id": "sbx_nested",
        "engine": {"profile": "/tmp/p.sb", "pgids": [1, 2]},
        "metadata": {"labels": ["a", "b"]},
        "timeout": None,
    }
    store.add(record)
    again = store.get("sbx_nested")
    assert again["engine"]["pgids"] == [1, 2]
    assert again["metadata"] == {"labels": ["a", "b"]}
    assert again["timeout"] is None


def test_atomic_write_leaves_no_temp_files(store):
    store.add({"id": "sbx_atomic"})
    leftovers = [p for p in store.root.iterdir() if p.name.startswith(".registry-")]
    assert leftovers == []


def test_registry_survives_concurrent_process_writes(store):
    """Parallel processes adding disjoint records must not lose updates.

    Store mutations hold an exclusive file lock for the read-modify-write
    cycle, so every record lands exactly once and the file stays valid JSON.
    """
    ids = [f"sbx_conc_{i}" for i in range(24)]
    half = 12
    script = (
        "import sys\n"
        "from loopbox.store import Store\n"
        "store = Store()\n"
        "for sid in sys.argv[1:]:\n"
        "    store.add({'id': sid})\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, *chunk],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
        )
        for chunk in (ids[:half], ids[half:])
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0
    data = json.loads(Path(store.path).read_text())
    assert set(ids) <= set(data)
