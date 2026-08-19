"""Unit tests for the registry store."""

from __future__ import annotations

import os
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
    import json

    store.add({"id": "sbx_json"})
    data = json.loads(Path(store.path).read_text())
    assert "sbx_json" in data
