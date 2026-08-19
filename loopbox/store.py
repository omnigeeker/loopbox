"""Persistent sandbox registry and on-disk layout.

Layout (root defaults to ``~/.loopbox``, override with ``LOOPBOX_HOME``)::

    sandboxes.json                  registry of sandbox records
    sandboxes/<id>/workspace/       writable workspace root of a sandbox
    sandboxes/<id>/profile.sb       generated Seatbelt profile
    snapshots/<id>/<snapshot_id>/   clonefile snapshots of a workspace

All writes are atomic (tmp file + rename) so a crashed loop never leaves a
half-written registry.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ENV_HOME = "LOOPBOX_HOME"
REGISTRY_FILE = "sandboxes.json"


def home() -> Path:
    """Return the Loopbox state root, creating it on first use."""
    root = Path(os.environ.get(ENV_HOME, "~/.loopbox")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    (root / "sandboxes").mkdir(exist_ok=True)
    (root / "snapshots").mkdir(exist_ok=True)
    return root


def new_id(prefix: str = "sbx") -> str:
    """Return a short, URL-safe sandbox id like ``sbx_9f2c41ab07d1``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sandbox_dir(sandbox_id: str) -> Path:
    return home() / "sandboxes" / sandbox_id


def workspace_dir(sandbox_id: str) -> Path:
    return sandbox_dir(sandbox_id) / "workspace"


def snapshot_root(sandbox_id: str) -> Path:
    return home() / "snapshots" / sandbox_id


class StoreError(Exception):
    """Raised when the registry cannot satisfy a request."""


class Store:
    """JSON-file registry of sandbox records."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or home()
        self.path = self.root / REGISTRY_FILE

    # -- internal ---------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise StoreError(f"corrupt registry at {self.path}")
        return data

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".registry-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- public API -------------------------------------------------------

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        sid = record["id"]
        if sid in data:
            raise StoreError(f"sandbox {sid} already exists")
        record.setdefault("created_at", time.time())
        data[sid] = record
        self._save(data)
        return record

    def get(self, sandbox_id: str) -> dict[str, Any]:
        data = self._load()
        if sandbox_id not in data:
            raise StoreError(f"sandbox {sandbox_id} not found")
        return data[sandbox_id]

    def update(self, sandbox_id: str, **fields: Any) -> dict[str, Any]:
        data = self._load()
        if sandbox_id not in data:
            raise StoreError(f"sandbox {sandbox_id} not found")
        data[sandbox_id].update(fields)
        self._save(data)
        return data[sandbox_id]

    def remove(self, sandbox_id: str) -> None:
        data = self._load()
        data.pop(sandbox_id, None)
        self._save(data)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load().values(), key=lambda r: r.get("created_at", 0))
