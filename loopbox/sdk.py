"""Public Python SDK for Loopbox sandboxes.

This module implements the surface promised by ``loopbox/__init__.py``::

    from loopbox import Sandbox, SandboxError

    sbx = Sandbox.create(template="seatbelt")
    result = sbx.commands.run("echo hello")
    sbx.files.write("notes/hello.txt", "hi")
    sbx.pause()
    clone = sbx.fork()
    sbx.resume()
    sbx.kill()

The SDK talks directly to the local :class:`~loopbox.store.Store` registry
and the execution backends -- no HTTP server is involved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from loopbox import store as store_mod
from loopbox.backends import ExecResult, backend_names, get_backend
from loopbox.store import Store, StoreError, workspace_dir


class SandboxError(Exception):
    """Raised for any SDK-level failure (registry or backend)."""


def _resolve_workspace_path(workspace: Path, path: str) -> Path:
    """Resolve ``path`` against the workspace and enforce containment.

    A leading ``/`` is treated as the workspace root (E2B-style absolute
    paths); ``..`` segments escaping the workspace are rejected. The
    workspace itself is resolved first because ``LOOPBOX_HOME`` may live
    under a symlinked directory (e.g. ``/tmp`` -> ``/private/tmp``).
    """
    workspace = workspace.resolve()
    candidate = (workspace / path.lstrip("/")).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise SandboxError(f"path {path!r} escapes the sandbox workspace")
    return candidate


class _Commands:
    """Command execution handle exposed as ``sandbox.commands``."""

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    def run(
        self,
        command: str | Sequence[str],
        timeout: float | None = None,
        envs: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        """Run one command inside the sandbox and capture its output.

        Args:
            command: A string is executed through the login shell
                (``/bin/zsh -lc``), matching E2B ``commands.run`` semantics;
                an argv sequence is executed directly without a shell.
            timeout: Per-command timeout in seconds. Defaults to the timeout
                given to :meth:`Sandbox.create` (``None`` means no limit).
            envs: Extra environment variables merged over the sandbox env.
            cwd: Working directory relative to the sandbox workspace.

        Returns:
            The backend's :class:`~loopbox.backends.base.ExecResult`.

        Raises:
            SandboxError: If the command is empty or execution fails.
        """
        if isinstance(command, str):
            argv = ["/bin/zsh", "-lc", command]
        else:
            argv = [str(a) for a in command]
        if not argv:
            raise SandboxError("command must not be empty")
        sbx = self._sandbox
        if timeout is None:
            timeout = sbx._record.get("timeout")
        sbx._require_not_killed()
        return sbx._call_backend(
            "exec", sbx._backend.exec, argv, cwd=cwd, env=envs, timeout=timeout
        )


class _Files:
    """Workspace file handle exposed as ``sandbox.files``.

    All paths are relative to the sandbox workspace root; absolute paths or
    ``..`` segments that would escape the workspace are rejected.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    def _resolve(self, path: str) -> Path:
        return _resolve_workspace_path(self._sandbox._workspace, path)

    def read(self, path: str, format: str = "text") -> str | bytes:  # noqa: A002 - E2B parity
        """Read a workspace file and return its contents.

        Args:
            path: Workspace-relative path.
            format: ``"text"`` (default) or ``"bytes"``.

        Raises:
            SandboxError: If the path escapes the workspace or cannot be read.
        """
        target = self._resolve(path)
        try:
            if format == "bytes":
                return target.read_bytes()
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise SandboxError(
                f"cannot read {path!r} in sandbox {self._sandbox.id}: {exc}"
            ) from exc

    def write(self, path: str, data: str | bytes) -> None:
        """Write ``data`` (text or bytes) to a workspace file.

        Missing parent directories are created automatically.

        Raises:
            SandboxError: If the path escapes the workspace or cannot be written.
        """
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                target.write_bytes(data)
            else:
                target.write_text(data, encoding="utf-8")
        except OSError as exc:
            raise SandboxError(
                f"cannot write {path!r} in sandbox {self._sandbox.id}: {exc}"
            ) from exc

    def list(self, path: str = ".") -> list[str]:
        """Return the sorted names of the entries in a workspace directory.

        Raises:
            SandboxError: If the path escapes the workspace or is not a directory.
        """
        target = self._resolve(path)
        try:
            return sorted(entry.name for entry in target.iterdir())
        except OSError as exc:
            raise SandboxError(
                f"cannot list {path!r} in sandbox {self._sandbox.id}: {exc}"
            ) from exc


class Sandbox:
    """A local sandbox with an isolated workspace and execution backend.

    Instances are obtained via :meth:`create`, :meth:`connect` or
    :meth:`fork`; the constructor is intentionally low-level and assumes an
    already-registered store record.
    """

    def __init__(self, record: dict[str, Any], store: Store | None = None) -> None:
        self._store = store or Store()
        self._record = record
        self._backend = self._backend_for(record.get("backend"))
        self._workspace = workspace_dir(record["id"])
        self.commands = _Commands(self)
        self.files = _Files(self)

    # -- construction -----------------------------------------------------

    @classmethod
    def create(
        cls,
        template: str | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        envs: dict[str, str] | None = None,
        network: str = "outbound",
        extra_rw: Sequence[str] = (),
    ) -> Sandbox:
        """Create a new sandbox.

        Args:
            template: Backend selector. ``None`` and ``"seatbelt"`` use the
                default Seatbelt backend; ``"vz"`` selects the experimental
                Virtualization.framework backend.
            timeout: Default per-command timeout in seconds (``None`` = none).
            metadata: Free-form user metadata stored with the sandbox record.
            envs: Default environment variables for commands in this sandbox.
            network: Network policy -- ``"outbound"`` (default), ``"all"`` or
                ``"deny"`` (backend-dependent).
            extra_rw: Additional paths the sandbox may read and write.

        Returns:
            The newly created, running sandbox.

        Raises:
            SandboxError: If the template is unknown or setup fails.
        """
        backend = cls._backend_for(template)
        record: dict[str, Any] = {
            "id": store_mod.new_id("sbx"),
            "backend": backend.name,
            "template": template,
            "status": "creating",
            "network": network,
            "timeout": timeout,
            "env": dict(envs or {}),
            "metadata": dict(metadata or {}),
            "extra_rw": list(extra_rw),
        }
        store = Store()
        try:
            backend.create(record)
        except Exception as exc:
            raise SandboxError(
                f"failed to set up sandbox {record['id']} "
                f"(backend {backend.name!r}): {exc}"
            ) from exc
        record["status"] = "running"
        try:
            store.add(record)
        except StoreError as exc:
            raise SandboxError(
                f"failed to register sandbox {record['id']}: {exc}"
            ) from exc
        return cls(record, store=store)

    @classmethod
    def connect(cls, sandbox_id: str) -> Sandbox:
        """Rehydrate an existing sandbox from the registry.

        Raises:
            SandboxError: If no sandbox with ``sandbox_id`` is registered.
        """
        store = Store()
        try:
            record = store.get(sandbox_id)
        except StoreError as exc:
            raise SandboxError(f"cannot connect to sandbox {sandbox_id!r}: {exc}") from exc
        return cls(record, store=store)

    @staticmethod
    def _backend_for(name: str | None):
        """Instantiate a backend by name, normalizing SDK errors."""
        try:
            return get_backend(name)
        except ValueError as exc:
            known = ", ".join(backend_names())
            raise SandboxError(
                f"unknown template {name!r} (available: {known})"
            ) from exc

    # -- identity ---------------------------------------------------------

    @property
    def id(self) -> str:
        """The sandbox id, e.g. ``sbx_9f2c41ab07d1``."""
        return self._record["id"]

    @property
    def metadata(self) -> dict[str, Any]:
        """User metadata stored with the sandbox record."""
        return dict(self._record.get("metadata") or {})

    @property
    def status(self) -> str:
        """Current registry status: ``running``, ``paused`` or ``killed``."""
        try:
            self._record = self._store.get(self.id)
        except StoreError:
            pass
        return self._record.get("status", "unknown")

    @property
    def is_running(self) -> bool:
        """Whether the registry currently marks this sandbox as ``running``."""
        return self.status == "running"

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        """Return all registered sandbox records, oldest first."""
        return Store().list()

    def set_timeout(self, timeout: float) -> None:
        """Update the default per-command timeout (E2B-compatible)."""
        self._record["timeout"] = timeout
        try:
            self._record = self._store.update(self.id, timeout=timeout)
        except StoreError as exc:
            raise SandboxError(f"failed to persist timeout of sandbox {self.id}: {exc}") from exc

    def info(self) -> dict[str, Any]:
        """Return an E2B-shaped info dict for this sandbox."""
        try:
            self._record = self._store.get(self.id)
        except StoreError:
            pass
        return {
            "sandbox_id": self.id,
            "template": self._record.get("template") or self._record.get("backend"),
            "status": self._record.get("status"),
            "started_at": self._record.get("created_at"),
            "metadata": dict(self._record.get("metadata") or {}),
            "workspace": str(self._workspace),
            "parent_id": self._record.get("parent_id"),
        }

    def __repr__(self) -> str:
        return (
            f"Sandbox(id={self.id!r}, backend={self._record.get('backend')!r}, "
            f"status={self._record.get('status')!r})"
        )

    # -- lifecycle --------------------------------------------------------

    def pause(self) -> None:
        """Freeze all sandbox activity; the sandbox stays resumable."""
        self._require_status("running", action="pause")
        self._call_backend("pause", self._backend.pause)
        self._set_status("paused")

    # E2B beta alias.
    beta_pause = pause

    def resume(self) -> None:
        """Resume a previously paused sandbox."""
        self._require_status("paused", action="resume")
        self._call_backend("resume", self._backend.resume)
        self._set_status("running")

    def snapshot(self, name: str | None = None) -> str:
        """Capture the current workspace state; returns the snapshot id."""
        self._require_not_killed()
        return self._call_backend("snapshot", self._backend.snapshot, name)

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List the metadata dicts of snapshots taken of this sandbox."""
        return self._call_backend("list snapshots", self._backend.list_snapshots)

    # Short alias used by the CLI and HTTP API.
    snapshots = list_snapshots

    def restore(self, snapshot_id: str) -> None:
        """Roll the sandbox workspace back to a previous snapshot."""
        self._require_not_killed()
        self._call_backend(f"restore snapshot {snapshot_id!r}", self._backend.restore, snapshot_id)

    def fork(self, snapshot_id: str | None = None) -> Sandbox:
        """Clone this sandbox (or one of its snapshots) into a new sandbox.

        Returns:
            The new child sandbox, already registered and running.
        """
        self._require_not_killed()
        child = self._call_backend("fork", self._backend.fork, snapshot_id)
        return Sandbox(child, store=self._store)

    def kill(self) -> None:
        """Stop everything and mark the sandbox as killed.

        The registry record and workspace are kept on disk for forensics;
        use the CLI (``loopbox rm --purge``) to delete them.
        """
        self._call_backend("kill", self._backend.kill)
        self._set_status("killed")

    # -- internal ---------------------------------------------------------

    def _require_not_killed(self) -> None:
        if self._record.get("status") == "killed":
            raise SandboxError(f"sandbox {self.id} has been killed")

    def _require_status(self, *allowed: str, action: str = "operate on") -> None:
        """Refresh state from the registry and require one of ``allowed``."""
        try:
            self._record = self._store.get(self.id)
        except StoreError as exc:
            raise SandboxError(f"cannot {action} sandbox {self.id}: {exc}") from exc
        status = self._record.get("status")
        if status not in allowed:
            raise SandboxError(
                f"cannot {action} sandbox {self.id}: status is {status!r}, "
                f"expected one of {allowed}"
            )

    def _set_status(self, status: str) -> None:
        self._record["status"] = status
        try:
            self._record = self._store.update(self.id, status=status)
        except StoreError as exc:
            raise SandboxError(f"failed to persist status of sandbox {self.id}: {exc}") from exc

    def _call_backend(self, action: str, fn, *args, **kwargs):
        """Invoke a backend operation, normalizing failures to SandboxError."""
        try:
            return fn(self._record, *args, **kwargs)
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError(
                f"{action} failed on sandbox {self.id} "
                f"(backend {self._backend.name!r}): {exc}"
            ) from exc
