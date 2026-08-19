# vzrunner

`vzrunner` is the small Swift helper that powers the experimental `vz` backend
of [loopbox](../loopbox). It wraps Apple's **Virtualization.framework** to boot
a minimal **ARM64 Linux** guest per loopbox sandbox and exposes the whole-machine
state operations (pause / snapshot / restore / kill) that a process-level
sandbox cannot provide.

- **Host:** macOS 14+ on Apple Silicon (arm64)
- **Guest:** ARM64 Linux (uncompressed kernel `Image` + raw rootfs disk)
- **Contract:** mirrors exactly what `loopbox/backends/vz.py` shells out to.

## Building

```sh
cd vzrunner
./build.sh
```

`build.sh` runs `swiftc -O` (whole-module optimization, deployment target
macOS 14, arm64) and writes the binary to:

```
vzrunner/.build/release/vzrunner
```

Requirements (checked by the script, fail fast with a clear message):

- `swiftc` from the Xcode Command Line Tools (`xcode-select --install`).
- macOS **14 (Sonoma) or later** — snapshot/restore use
  `saveMachineStateToURL:` / `restoreMachineStateFromURL:completionHandler:`,
  which are macOS 14 APIs.
- **arm64** — this builds and targets Apple Silicon only.

No SwiftPM manifest is needed; the program is a single file
(`Sources/vzrunner/main.swift`) built directly with `swiftc`.

> **Entitlements.** To actually *boot* a VM on macOS 14+, the running process
> needs the `com.apple.security.virtualization` entitlement. `build.sh` ad-hoc
> signs the binary so it loads; on some hosts you may also need to grant the
> entitlement (or run under `vtool`/`codesign` with a provisioning profile).
> Compiling and CLI parsing never require it — only `vzrunner exec`,
> which starts a VM, does.

## CLI contract

`loopbox/backends/vz.py` invokes these exact subcommands. `vzrunner` also
implements `status` and an internal `run-manager` entry point.

```
vzrunner exec     --bundle <bundle> -- <argv...>
vzrunner pause    --bundle <bundle>
vzrunner resume   --bundle <bundle>
vzrunner snapshot --bundle <bundle> --name <name>
vzrunner restore  --bundle <bundle> --name <name>
vzrunner kill     --bundle <bundle>
vzrunner status   --bundle <bundle>
```

Global flags accepted before the `--` separator: `--bundle` (required),
`--name` (snapshot/restore), `--log-level error|warning|info|debug`. Everything
after a bare `--` is the guest command line for `exec`.

Exit status is `0` on success and non-zero with a human-readable message on
stderr otherwise, so `vz.py`'s `subprocess` wrapper surfaces failures cleanly.

## Guest bundle format

`--bundle` points to a directory with this layout. `vzrunner exec` creates the
writable working directories on first use; the guest artifacts (`kernel`,
`disk.img`) must be provided by the caller (see `loopbox` docs/vz.md).

```
<bundle>/
    guest/
        kernel            # REQUIRED: uncompressed arm64 Linux kernel (Image)
        initramfs         # OPTIONAL: gzip'd initramfs / initrd
    disk.img              # REQUIRED: raw (sparse) rootfs disk image
    workspace/            # host dir shared into the guest via VirtioFS
    snapshots/
        <name>/
            machine-state # whole-VM checkpoint from saveMachineStateToURL (macOS 14+)
            disk.img      # APFS copy-on-write clone of the live disk at snapshot time
    run/
        manager.sock      # AF_UNIX control socket of the resident VM manager
        manager.pid       # pidfile of the resident VM manager
```

### Guest configuration

`vzrunner` builds the `VZVirtualMachineConfiguration` as follows:

- **Boot loader** — `VZLinuxBootLoader` with `guest/kernel`; `guest/initramfs`
  is attached when present. The kernel command line is `console=hvc0` (plus the
  exec shim, see below).
- **CPU / memory** — up to 4 vCPUs (clamped to the host) and 2 GiB RAM (raised
  to the framework minimum if larger).
- **Storage** — `disk.img` attached read-write through a
  `VZVirtioBlockDeviceConfiguration` (guest sees it as `/dev/vda`).
- **Workspace share** — `VZVirtioFileSystemDeviceConfiguration` (macOS 12+)
  exposing the host `workspace/` directory inside the guest under the mount tag
  **`workspace`**. The guest mounts it with
  `mount -t virtiofs workspace /mnt/workspace`.
- **Networking** — `VZVirtioNetworkDeviceConfiguration` on a
  `VZNATNetworkDeviceAttachment` for outbound-only NAT networking.
- **Console** — `VZVirtioConsoleDeviceSerialPortConfiguration` bound to the
  manager's stdout (`hvc0`), so boot logs and `exec` output are visible to the
  caller.
- **Entropy / balloon** — virtio entropy and a traditional memory balloon
  device are attached.

## How the subcommands work

`vzrunner exec` ensures a resident **VM manager** process owns the bundle's
`VZVirtualMachine` (Virtualization.framework requires each VM to live on the
main runloop of the process that created it). The CLI forwards `pause` /
`resume` / `snapshot` / `restore` / `kill` / `status` to that manager as
newline-delimited JSON over `run/manager.sock`:

```
{"op": "pause"}            ->  {"ok": true} | {"ok": false, "error": "..."}
{"op": "snapshot", "name": "<name>"}
```

- **pause** — `vm.pause()` (async), gated on `vm.canPause`.
- **resume** — `vm.resume()` (async), gated on `vm.canResume`.
- **snapshot** — `vm.saveMachineStateTo(url:)` into `snapshots/<name>/machine-state` (macOS 14+),
  then an APFS copy-on-write `clonefile()` of the live `disk.img` to
  `snapshots/<name>/disk.img`, then resumes the VM.
- **restore** — clones the snapshot's `disk.img` back over the live disk and
  releases the current VM object so the next `exec` boots from the restored
  state (see the limitation below).
- **kill** — asks the manager to stop the VM and exit, then removes the stale
  socket/pidfile. Safe to run when no manager is alive.

`fork` is **not** a vzrunner subcommand: `vz.py` implements it Python-side with
`cp -Rc`, APFS-cloning the whole bundle directory (including snapshots) so the
child shares unchanged blocks with its parent.

## Guest-side exec (current limitation)

**There is no in-guest agent yet.** True E2B-style `exec` requires a guest
process reachable over a vsock / virtio-console channel; building that agent is
out of scope for this single-file helper. So `exec` today works by:

1. Terminating any manager currently running the bundle.
2. Booting the guest with an `init=` kernel-command-line shim that runs the
   requested command as PID 1 and then calls `poweroff -f`.
3. Streaming boot + command output through the manager's stdout / serial console.

Consequences you must know:

- Command output is **not captured** into `ExecResult.stdout` by `vz.py` yet —
  it is written to the manager process's serial/stdout.
- Each `exec` is a fresh boot, so there is no persistent PID 1 / login session
  between commands, and `pause`/`resume` cannot carry a running shell across
  `exec` calls.
- `restore` therefore restores the *disk* and releases the VM; resuming an
  exact in-memory checkpoint across an exec boundary needs the agent.

To go further, drop a tiny static init/agent into `guest/` that listens on
`virtio-vsock`, and have `exec` send the argv over that channel instead of the
`init=` shim. The bundle and socket plumbing here already support that swap.

## Relationship to loopbox

`loopbox.backends.vz.VzBackend` locates the binary at
`<repo>/vzrunner/.build/release/vzrunner` (falling back to `which vzrunner`).
Until it is built, the backend raises a clear `FileNotFoundError` pointing here.
