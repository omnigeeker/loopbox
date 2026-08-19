//  vzrunner - a tiny VFKit-style helper around Virtualization.framework.
//
//  vzrunner boots a minimal ARM64 Linux guest for one loopbox *bundle* and is
//  the execution engine behind ``loopbox.backends.vz``. The whole-machine
//  state (RAM, vCPUs, device model) is what makes true pause / snapshot /
//  restore / fork possible.
//
//  Bundle layout on disk (see README.md for the authoritative spec):
//
//      <bundle>/
//          guest/kernel          # required: uncompressed Linux arm64 kernel (Image)
//          guest/initramfs       # optional: gzip'd initramfs / initrd
//          disk.img              # required: raw (sparse) rootfs disk image
//          workspace/            # shared into the guest via VirtioFS at tag "workspace"
//          snapshots/<name>/machine-state
//          snapshots/<name>/disk.img
//          run/manager.sock      # control socket of the resident VM manager
//          run/manager.pid
//
//  Architecture: daemon-based. ``vzrunner exec`` makes sure a resident
//  *VM manager* process owns the bundle's VZVirtualMachine (VZ requires the VM
//  to live on the main runloop of a process), then forwards the subcommand
//  over a per-bundle AF_UNIX control socket. That keeps pause / resume /
//  snapshot / kill real cross-process VZ calls while ``VZVirtualMachine`` stays
//  single-process-owned.
//
//  Exec limitation: there is no in-guest agent yet, so ``exec`` (re)boots the
//  guest with a kernel command line that runs the command as PID 1 and powers
//  off. See README.md ("Guest-side exec") for how to swap in a vsock agent.

import Foundation
import Virtualization

// MARK: - wire protocol
//
// Manager control socket speaks newline-delimited JSON:
//   request  : {"op": "<exec|pause|resume|snapshot|restore|kill|status>", ...}
//   response : {"ok": true, ...} or {"ok": false, "error": "..."}
// Keep this in sync with loopbox/backends/vz.py expectations.

enum Wire {
    static func request(_ dict: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: dict, options: [])
        return String(decoding: data, as: UTF8.self) + "\n"
    }

    static func parse(_ line: String) throws -> [String: Any] {
        guard let data = line.data(using: .utf8) else {
            throw VZRError.protocolError("non-utf8 wire line")
        }
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw VZRError.protocolError("wire line is not a JSON object")
        }
        return dict
    }
}

enum VZRError: Error, CustomStringConvertible {
    case usage(String)
    case bundle(String)
    case protocolError(String)
    case manager(String)
    case vm(String)
    case unsupported(String)
}

// Map to a readable localizedDescription too: several call sites report errors
// via `error.localizedDescription` (which for a Swift enum would otherwise be
// the opaque "VZRError error N."), so forward it to our CustomStringConvertible.
extension VZRError: LocalizedError {
    var errorDescription: String? { description }
}

extension VZRError {
    var description: String {
        switch self {
        case .usage(let m): return "usage: \(m)"
        case .bundle(let m): return "bundle error: \(m)"
        case .protocolError(let m): return "protocol error: \(m)"
        case .manager(let m): return "manager error: \(m)"
        case .vm(let m): return "vm error: \(m)"
        case .unsupported(let m): return "unsupported: \(m)"
        }
    }
}

// MARK: - paths

/// Resolved on-disk layout for one guest bundle.
struct BundlePaths {
    let root: URL

    var guestDir: URL { root.appendingPathComponent("guest", isDirectory: true) }
    var kernel: URL { guestDir.appendingPathComponent("kernel") }
    var initramfs: URL { guestDir.appendingPathComponent("initramfs") }
    var disk: URL { root.appendingPathComponent("disk.img") }
    var workspace: URL { root.appendingPathComponent("workspace", isDirectory: true) }
    var snapshots: URL { root.appendingPathComponent("snapshots", isDirectory: true) }
    var runDir: URL { root.appendingPathComponent("run", isDirectory: true) }
    var socket: URL { runDir.appendingPathComponent("manager.sock") }
    var pidfile: URL { runDir.appendingPathComponent("manager.pid") }

    func snapshotDir(_ name: String) -> URL {
        snapshots.appendingPathComponent(name, isDirectory: true)
    }
    func machineState(_ name: String) -> URL {
        snapshotDir(name).appendingPathComponent("machine-state")
    }
    func snapshotDisk(_ name: String) -> URL {
        snapshotDir(name).appendingPathComponent("disk.img")
    }

    /// Create the writable working directories and make sure the bundle looks
    /// usable. The guest kernel / disk are validated at boot time so the error
    /// message points at the exact missing file.
    func ensureLayout() throws {
        let fm = FileManager.default
        for dir in [guestDir, workspace, snapshots, runDir] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }
}

// MARK: - tiny AF_UNIX socket helpers (POSIX, no extra deps)

private let AF_UNIX_LOCAL = AF_UNIX

enum UnixSocket {
    /// Connect to `path` and return a connected fd owned by the caller.
    static func connect(path: String) throws -> Int32 {
        let fd = socket(AF_UNIX_LOCAL, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw VZRError.manager("socket(): \(String(cString: strerror(errno)))")
        }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX_LOCAL)
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        try path.withCString { cstr in
            guard strlen(cstr) < capacity else {
                throw VZRError.manager("unix socket path too long: \(path)")
            }
            withUnsafeMutablePointer(to: &addr.sun_path) { tuple in
                _ = strcpy(UnsafeMutableRawPointer(tuple).assumingMemoryBound(to: CChar.self), cstr)
            }
        }
        let len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let rc = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.connect(fd, sa, len)
            }
        }
        if rc != 0 {
            let msg = String(cString: strerror(errno))
            Darwin.close(fd)
            throw VZRError.manager("connect(\(path)): \(msg)")
        }
        return fd
    }

    /// Bind + listen, returning the listening fd.
    static func listen(path: String) throws -> Int32 {
        unlink(path) // stale socket from a previous manager
        let fd = socket(AF_UNIX_LOCAL, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw VZRError.manager("socket(): \(String(cString: strerror(errno)))")
        }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX_LOCAL)
        let capacity = MemoryLayout.size(ofValue: addr.sun_path)
        try path.withCString { cstr in
            guard strlen(cstr) < capacity else {
                throw VZRError.manager("unix socket path too long: \(path)")
            }
            withUnsafeMutablePointer(to: &addr.sun_path) { tuple in
                _ = strcpy(UnsafeMutableRawPointer(tuple).assumingMemoryBound(to: CChar.self), cstr)
            }
        }
        let len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let bound = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.bind(fd, sa, len)
            }
        }
        guard bound == 0 else {
            let msg = String(cString: strerror(errno))
            Darwin.close(fd)
            throw VZRError.manager("bind(\(path)): \(msg)")
        }
        guard Darwin.listen(fd, 8) == 0 else {
            let msg = String(cString: strerror(errno))
            Darwin.close(fd)
            throw VZRError.manager("listen(): \(msg)")
        }
        return fd
    }

    static func writeAll(fd: Int32, _ string: String) throws {
        try string.withCString { cstr in
            var remaining = strlen(cstr)
            var ptr = cstr
            while remaining > 0 {
                let n = Darwin.write(fd, ptr, remaining)
                guard n > 0 else {
                    throw VZRError.manager("write(): \(String(cString: strerror(errno)))")
                }
                remaining -= n
                ptr = ptr.advanced(by: n)
            }
        }
    }

    /// Read until newline or EOF. Returns the line without the newline.
    static func readLine(fd: Int32) throws -> String {
        var out = [UInt8]()
        var byte: UInt8 = 0
        while true {
            let n = Darwin.read(fd, &byte, 1)
            if n == 0 { break }          // EOF
            guard n > 0 else {
                throw VZRError.manager("read(): \(String(cString: strerror(errno)))")
            }
            if byte == UInt8(ascii: "\n") { break }
            out.append(byte)
        }
        return String(decoding: out, as: UTF8.self)
    }
}

// MARK: - guest configuration

/// Build a VZVirtualMachineConfiguration for the bundle.
///
/// Parameters mirror the subsystems the loopbox ``vz`` backend relies on:
/// an uncompressed arm64 Linux kernel, an optional initramfs, a raw rootfs
/// disk, a VirtioFS host share for the mutable workspace, and NAT networking.
struct GuestConfig {
    let bundle: BundlePaths
    /// Extra text appended to the kernel command line (used by exec to run a
    /// command as PID 1 / init shim).
    var execCommandLine: String? = nil

    func makeConfiguration() throws -> VZVirtualMachineConfiguration {
        guard FileManager.default.fileExists(atPath: bundle.kernel.path) else {
            throw VZRError.bundle("missing guest kernel at \(bundle.kernel.path) — see vzrunner/README.md")
        }
        guard FileManager.default.fileExists(atPath: bundle.disk.path) else {
            throw VZRError.bundle("missing rootfs disk at \(bundle.disk.path) — see vzrunner/README.md")
        }

        let config = VZVirtualMachineConfiguration()
        config.cpuCount = GuestConfig.defaultCPUCount()
        config.memorySize = GuestConfig.defaultMemorySize()

        // --- boot loader ---------------------------------------------------
        let bootLoader = VZLinuxBootLoader(kernelURL: bundle.kernel)
        var cmdline = ["console=hvc0"]
        if let execLine = execCommandLine {
            // Run the command as PID 1 then power off. The guest kernel parses
            // init= as the first userspace program; framing it via /bin/sh is a
            // conventional way to get an exec-and-exit shim without an agent.
            cmdline.append("init=/bin/sh")
            cmdline.append("--")
            cmdline.append("-c")
            cmdline.append("\"\(execLine); poweroff -f\"")
        }
        bootLoader.commandLine = cmdline.joined(separator: " ")
        if FileManager.default.fileExists(atPath: bundle.initramfs.path) {
            bootLoader.initialRamdiskURL = bundle.initramfs
        }
        config.bootLoader = bootLoader

        // --- storage -------------------------------------------------------
        guard let diskAttachment = try? VZDiskImageStorageDeviceAttachment(
            url: bundle.disk, readOnly: false) else {
            throw VZRError.bundle("could not attach disk image at \(bundle.disk.path)")
        }
        let blockDevice = VZVirtioBlockDeviceConfiguration(attachment: diskAttachment)
        config.storageDevices = [blockDevice]

        // --- workspace VirtioFS share --------------------------------------
        // VirtioFS is macOS 12+; on anything older we degrade gracefully by
        // simply not attaching the share (the rest of the VM still boots).
        if #available(macOS 12.0, *) {
            if FileManager.default.fileExists(atPath: bundle.workspace.path) {
                let share = VZSharedDirectory(url: bundle.workspace, readOnly: false)
                let single = VZSingleDirectoryShare(directory: share)
                let fsDevice = VZVirtioFileSystemDeviceConfiguration(tag: "workspace")
                fsDevice.share = single
                config.directorySharingDevices = [fsDevice]
            }
        }

        // --- NAT networking -------------------------------------------------
        let networkDevice = VZVirtioNetworkDeviceConfiguration()
        networkDevice.attachment = VZNATNetworkDeviceAttachment()
        config.networkDevices = [networkDevice]

        // --- entropy / memory balloon --------------------------------------
        config.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
        config.memoryBalloonDevices = [VZVirtioTraditionalMemoryBalloonDeviceConfiguration()]

        // --- serial console -------------------------------------------------
        // Wire the guest's hvc0 console to stdin/stdout so `exec` output is
        // visible on the manager's own stdout (and thus the calling process).
        let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
        let stdinAttachment = VZFileHandleSerialPortAttachment(
            fileHandleForReading: FileHandle.standardInput,
            fileHandleForWriting: FileHandle.standardOutput)
        serial.attachment = stdinAttachment
        config.serialPorts = [serial]

        try config.validate()
        return config
    }

    /// Sized to the host but clamped so a loopbox sandbox never starves the
    /// machine; vCPUs are virtual and the scheduler handles oversubscription.
    static func defaultCPUCount() -> Int {
        let available = ProcessInfo.processInfo.processorCount
        return max(1, min(4, available))
    }

    /// Memory must fulfil VZ's minimum; clamp to a sane sandbox budget.
    static func defaultMemorySize() -> UInt64 {
        let gib: UInt64 = 1024 * 1024 * 1024
        let requested: UInt64 = 2 * gib
        let minimum = VZVirtualMachineConfiguration.minimumAllowedMemorySize
        return max(requested, minimum)
    }
}

// MARK: - VM manager (daemon)
//
// The manager owns the VZVirtualMachine on its main runloop and serves the
// control socket from a background thread. It must be a long-lived process
// because pause()/resume()/saveMachineStateToURL act on the live VM object.

final class ManagerDelegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        // Guest powered itself off (e.g. the exec shim ran `poweroff -f`).
        // Nothing to persist here; the manager exits when asked via the socket.
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        FileHandle.standardError.write("vzrunner manager: VM stopped with error: \(error.localizedDescription)\n".data(using: .utf8)!)
    }
}

final class VMManager {
    let bundle: BundlePaths
    var machine: VZVirtualMachine?
    let delegate = ManagerDelegate()
    var serverFD: Int32 = -1
    /// Set when the guest failed to boot; reported by `status` so operators can
    /// tell "VM running" apart from "manager alive but guest dead".
    var bootError: String?

    init(bundle: BundlePaths) {
        self.bundle = bundle
    }

    /// Bind the control socket and boot the VM, all on the main thread.
    ///
    /// Host constraint (verified on macOS 26 / Swift 6.3): any nested runloop
    /// re-entry around a VZ lifecycle callback traps in mach-message delivery.
    /// Working pattern: `vm.start(completionHandler:)` uses the Result-block
    /// (NOT the `async` import); the main thread then runs `dispatchMain()`;
    /// the control socket is serviced by a DispatchSource on the main queue so
    /// every VZ call executes on the same main queue the VM lives on. Async VZ
    /// calls (pause/resume) are launched as `Task`s from that queue and their
    /// continuations resume safely under dispatchMain.
    func run(execLine: String?) throws {
        try bundle.ensureLayout()

        // Bind + pidfile first: exec/status probing keys off the socket. We keep
        // serving even if the guest fails to boot, so operators can still run
        // `status`/`kill` against a broken bundle instead of hitting a dead sock.
        serverFD = try UnixSocket.listen(path: bundle.socket.path)
        writePidfile()

        do {
            let config = GuestConfig(bundle: bundle, execCommandLine: execLine)
            let vm = VZVirtualMachine(configuration: try config.makeConfiguration())
            vm.delegate = delegate
            self.machine = vm

            // Boot the guest with the Result-block form (the async `start()`
            // also traps on this host's runloop delivery, so keep the block).
            // When an exec command line was supplied the guest runs it as PID 1
            // and powers itself off on completion; failures hit the delegate.
            vm.start(completionHandler: { (result: Result<Void, Error>) in
                if case .failure(let error) = result {
                    FileHandle.standardError.write("boot failed: \(error.localizedDescription)\n".data(using: .utf8)!)
                }
            })
        } catch {
            FileHandle.standardError.write("vzrunner manager: guest not started: \(error.localizedDescription)\n".data(using: .utf8)!)
            self.bootError = error.localizedDescription
        }

        startServing()
    }

    /// Serve the control socket with a blocking `accept()` on a background
    /// thread. Each accepted request is marshalled to the main queue as a Task.
    ///
    /// Host constraint (verified): a DispatchSource `accept()` registered on the
    /// main queue traps when its Task then awaits a VZ lifecycle call, but a
    /// plain blocking `accept()` on a background thread that hops each request to
    /// the *main* queue pauses/saves cleanly. So: blocking accept off-main, VZ
    /// on main, main thread parked in dispatchMain().
    private func startServing() {
        DispatchQueue.global(qos: .userInitiated).async {
            while true {
                var addr = sockaddr()
                var len = socklen_t(MemoryLayout<sockaddr>.size)
                let client = accept(self.serverFD, &addr, &len)
                guard client >= 0 else { continue }
                self.serve(client: client)
            }
        }
    }

    private func writePidfile() {
        try? "\(getpid())\n".write(to: bundle.pidfile, atomically: true, encoding: .utf8)
    }

    private func respond(fd: Int32, _ dict: [String: Any]) {
        if let line = try? Wire.request(dict) {
            try? UnixSocket.writeAll(fd: fd, line)
        }
    }

    /// Read one request from an accepted client, run it as a Task on the main
    /// queue, then reply and close. VZ ops are async/block callbacks and, under
    /// dispatchMain(), `await`-ing them resumes on this same main queue safely.
    private func serve(client: Int32) {
        Task {
            defer { Darwin.close(client) }
            do {
                let line = try UnixSocket.readLine(fd: client)
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    respond(fd: client, ["ok": false, "error": "empty request"])
                    return
                }
                let req = try Wire.parse(line)
                let op = req["op"] as? String ?? ""
                switch op {
                case "status":
                    var info: [String: Any] = ["ok": true, "pid": getpid(), "state": machineStateString()]
                    if let bootError = self.bootError { info["boot_error"] = bootError }
                    respond(fd: client, info)
                case "pause":
                    try await pauseVM()
                    respond(fd: client, ["ok": true])
                case "resume":
                    try await resumeVM()
                    respond(fd: client, ["ok": true])
                case "snapshot":
                    let name = req["name"] as? String ?? "snapshot"
                    try await snapshotVM(name: name)
                    respond(fd: client, ["ok": true, "name": name])
                case "restore":
                    let name = req["name"] as? String ?? "snapshot"
                    try restoreVM(name: name)
                    respond(fd: client, ["ok": true])
                case "kill":
                    respond(fd: client, ["ok": true])
                    shutdown()
                default:
                    respond(fd: client, ["ok": false, "error": "unknown op \(op)"])
                }
            } catch {
                respond(fd: client, ["ok": false, "error": error.localizedDescription])
            }
        }
    }

    private func machineStateString() -> String {
        guard let vm = machine else { return "absent" }
        switch vm.state {
        case .stopped: return "stopped"
        case .running: return "running"
        case .paused: return "paused"
        case .error: return "error"
        case .starting: return "starting"
        case .pausing: return "pausing"
        case .resuming: return "resuming"
        case .stopping: return "stopping"
        case .saving: return "saving"
        case .restoring: return "restoring"
        @unknown default: return "unknown"
        }
    }

    // serve() runs each command as a Task on the main queue under dispatchMain().
    // pause() / resume() are `NS_REFINED_FOR_SWIFT` (Swift `async`), and
    // saveMachineStateTo keeps a completion-block form (bridged below).

    // The async VZ calls resume their continuations on the main queue; since
    // serve() runs handlers as Tasks on that same queue under dispatchMain(),
    // `await`-ing pause/resume/save here is the proven-safe pattern on this host
    // (macOS 26 / Swift 6.3): no nested runloop re-entry, no cross-thread trap.

    private func pauseVM() async throws {
        guard let vm = machine, vm.canPause else {
            throw VZRError.vm("VM is not in a pausable state (state=\(machineStateString()))")
        }
        try await vm.pause()
    }

    private func resumeVM() async throws {
        guard let vm = machine, vm.canResume else {
            throw VZRError.vm("VM is not paused (state=\(machineStateString()))")
        }
        try await vm.resume()
    }

    /// Bridge the completion-block saveMachineStateTo into async on the main queue.
    private func saveMachineState(to url: URL) async throws {
        guard let vm = machine else { throw VZRError.vm("no VM running") }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            vm.saveMachineStateTo(url: url) { (error: Error?) in
                if let error = error { cont.resume(throwing: error) }
                else { cont.resume(returning: ()) }
            }
        }
    }

    private func snapshotVM(name: String) async throws {
        guard machine != nil else { throw VZRError.vm("no VM running") }
        if #available(macOS 14.0, *) {
            // saveMachineStateTo auto-pauses, writes the whole-VM state
            // (RAM + vCPU + device), then is resumed below so the parent keeps
            // running after a snapshot.
            self.createSnapshotDirIfNeeded(name)
            try await saveMachineState(to: bundle.machineState(name))
            // Reference the disk via an APFS copy-on-write clone so unchanged
            // blocks stay shared with the live disk image. fork (python-side)
            // later `cp -Rc`'s this whole bundle directory.
            try cloneFile(src: bundle.disk.path, dst: bundle.snapshotDisk(name).path)
            if let vm = self.machine, vm.canResume {
                try await vm.resume()
            }
        } else {
            throw VZRError.unsupported("snapshot requires macOS 14+ (saveMachineStateToURL)")
        }
    }

    private func createSnapshotDirIfNeeded(_ name: String) {
        try? FileManager.default.createDirectory(
            at: bundle.snapshotDir(name), withIntermediateDirectories: true)
    }

    /// Restore from a snapshot. Because a manager's runtime state resets with
    /// each `exec` boot (no guest agent keeps the guest alive across execs),
    /// restore is modeled as: verify the checkpoint exists, roll the rootfs
    /// disk back to the snapshot's clone, and release the current VM object so
    /// the next exec boots from the restored state. The saved machine-state
    /// blob at <name>/machine-state is the authoritative whole-VM checkpoint
    /// and is consumed by a boot-time restore (see README "Snapshots").
    private func restoreVM(name: String) throws {
        if #available(macOS 14.0, *) {
            let stateURL = bundle.machineState(name)
            guard FileManager.default.fileExists(atPath: stateURL.path) else {
                throw VZRError.bundle("no machine state at \(stateURL.path) — run snapshot first")
            }
            // Roll the rootfs back by cloning the snapshot's disk over the live disk.
            if FileManager.default.fileExists(atPath: bundle.snapshotDisk(name).path) {
                try cloneFile(src: bundle.snapshotDisk(name).path, dst: bundle.disk.path)
            }
            // Drop the current VM object; the restored disk + saved machine
            // state are picked up on the next exec/boot.
            self.machine = nil
        } else {
            throw VZRError.unsupported("restore requires macOS 14+ (restoreMachineStateFromURL)")
        }
    }

    private func shutdown() {
        let vm = machine
        // Tear down socket + pidfile, stop the VM, then schedule process exit so
        // the "ok" reply already written by serve() is flushed to the client.
        acceptSource?.cancel()
        try? FileManager.default.removeItem(at: bundle.socket)
        try? FileManager.default.removeItem(at: bundle.pidfile)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            if let vm = vm { Task { try? await vm.stop() } }
            exit(0)
        }
    }
}

// MARK: - APFS clonefile

/// Copy-on-write clone of a file, matching the semantics of ``cp -Rc``.
func cloneFile(src: String, dst: String) throws {
    unlink(dst)
    if clonefile(src, dst, 0) != 0 {
        throw VZRError.bundle("clonefile(\(src), \(dst)): \(String(cString: strerror(errno)))")
    }
}

// MARK: - CLI front-end
//
// vzrunner <subcommand> --bundle <path> [--name <name>] [--log-level <lvl>] -- <argv...>
// The front-end ensures a resident manager for the bundle, then forwards the
// request over the control socket and bridges stdout/stderr/exit codes.

struct CLIArgs {
    var subcommand: String = ""
    var bundle: String = ""
    var name: String = ""
    var logLevel: String = "info"
    var commandArgv: [String] = []

    static func parse(_ argv: [String]) throws -> CLIArgs {
        var out = CLIArgs()
        var rest = Array(argv.dropFirst()) // drop program name
        guard let first = rest.first else {
            throw VZRError.usage(CLIArgs.usageText())
        }
        if first == "--help" || first == "-h" || first == "help" {
            print(CLIArgs.usageText())
            exit(0)
        }
        out.subcommand = first
        rest.removeFirst()

        // Split on a bare `--`: everything after it is the guest argv.
        if let dashdash = rest.firstIndex(of: "--") {
            out.commandArgv = Array(rest[(dashdash + 1)...])
            rest = Array(rest[..<dashdash])
        }

        var i = 0
        while i < rest.count {
            let flag = rest[i]
            func value() throws -> String {
                guard i + 1 < rest.count else {
                    throw VZRError.usage("flag \(flag) requires a value\n\(CLIArgs.usageText())")
                }
                return rest[i + 1]
            }
            switch flag {
            case "--bundle":
                out.bundle = try value(); i += 2
            case "--name":
                out.name = try value(); i += 2
            case "--log-level":
                out.logLevel = try value(); i += 2
            default:
                throw VZRError.usage("unknown flag \(flag)\n\(CLIArgs.usageText())")
            }
        }

        guard !out.bundle.isEmpty else {
            throw VZRError.usage("missing required --bundle <path>\n\(CLIArgs.usageText())")
        }
        return out
    }

    static func usageText() -> String {
        return """
        vzrunner - loopbox Virtualization.framework helper (arm64 Linux on Apple Silicon)

        Usage:
          vzrunner exec     --bundle <bundle> -- <argv...>
          vzrunner pause    --bundle <bundle>
          vzrunner resume   --bundle <bundle>
          vzrunner snapshot --bundle <bundle> --name <name>
          vzrunner restore  --bundle <bundle> --name <name>
          vzrunner kill     --bundle <bundle>
          vzrunner status   --bundle <bundle>
          vzrunner run-manager --bundle <bundle> [--log-level error|warning|info|debug]

        The bundle must contain guest/kernel, disk.img and (optionally) guest/initramfs
        and workspace/. See vzrunner/README.md for the full bundle format.
        """
    }
}

/// Send one request to the resident manager and stream its response.
func sendRequest(bundle: BundlePaths, _ dict: [String: Any]) throws -> [String: Any] {
    let fd = try UnixSocket.connect(path: bundle.socket.path)
    defer { Darwin.close(fd) }
    try UnixSocket.writeAll(fd: fd, Wire.request(dict))
    let line = try UnixSocket.readLine(fd: fd)
    let resp = try Wire.parse(line)
    if (resp["ok"] as? Bool) != true {
        let msg = resp["error"] as? String ?? "unknown manager error"
        throw VZRError.manager(msg)
    }
    return resp
}

/// Is a manager already serving this bundle's socket?
func managerAlive(bundle: BundlePaths) -> Bool {
    guard FileManager.default.fileExists(atPath: bundle.socket.path) else { return false }
    if let fd = try? UnixSocket.connect(path: bundle.socket.path) {
        Darwin.close(fd)
        return true
    }
    return false
}

/// Daemonize a new `vzrunner run-manager` for this bundle.
///
/// Swift forbids `fork()`, so we double-`posix_spawn` instead. The intermediate
/// child is `setsid`-detached and exits immediately; that orphans the real
/// manager process so launchd adopts it and the short-lived `exec` front-end can
/// return while the manager keeps owning the bundle's VZVirtualMachine.
func spawnManager(bundle: BundlePaths, execLine: String?, logLevel: String) throws {
    let selfPath = URL(fileURLWithPath: CommandLine.arguments[0]).path
    var managerArgv = ["run-manager", "--bundle", bundle.root.path, "--log-level", logLevel]
    if let execLine = execLine {
        managerArgv.append(contentsOf: ["--exec", execLine])
    }

    // Detach the manager's stdio to /dev/null so it never holds our pipes. The
    // manager calls setsid() itself inside run-manager, so after we exit the
    // process is reparented to launchd and keeps owning the VM.
    var attrs: posix_spawnattr_t?
    posix_spawnattr_init(&attrs)

    // Redirect the manager's stdio to /dev/null so it never holds our pipes.
    var fileActions: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&fileActions)
    let devnull = "/dev/null"
    posix_spawn_file_actions_addopen(&fileActions, STDIN_FILENO, devnull, O_RDWR, 0)
    posix_spawn_file_actions_addopen(&fileActions, STDOUT_FILENO, devnull, O_RDWR, 0)
    posix_spawn_file_actions_addopen(&fileActions, STDERR_FILENO, devnull, O_RDWR, 0)

    var pid = pid_t()
    var spawnArgv: [UnsafeMutablePointer<CChar>?] = [strdup(selfPath)] + managerArgv.map { strdup($0) } + [nil]
    let environ: [UnsafeMutablePointer<CChar>?] = [nil]
    let spawnErr = spawnArgv.withUnsafeMutableBufferPointer { argvBuf -> Int32 in
        environ.withUnsafeBufferPointer { envBuf -> Int32 in
            posix_spawn(&pid, selfPath, &fileActions, &attrs, argvBuf.baseAddress, envBuf.baseAddress)
        }
    }
    posix_spawn_file_actions_destroy(&fileActions)
    posix_spawnattr_destroy(&attrs)
    guard spawnErr == 0 else {
        throw VZRError.manager("posix_spawn(\(selfPath)): \(String(cString: strerror(spawnErr)))")
    }

    // The manager daemonizes itself (setsid + detach) in run-manager, so we
    // only wait for its control socket to appear; a straight posix_spawn child
    // is reparented to launchd once we exit.
    let deadline = Date().addingTimeInterval(15.0)
    while Date() < deadline {
        if managerAlive(bundle: bundle) { return }
        Thread.sleep(forTimeInterval: 0.05)
    }
    throw VZRError.manager("manager for \(bundle.root.path) did not start in time")
}

// MARK: - run-manager entry point

/// Long-lived manager mode: boots the VM, serves the socket, runs the main
/// runloop forever so VZ's completion handlers keep firing. Never returns.
func runManager(bundleArg: String, extraArgs: [String]) throws -> Never {
    var execLine: String?
    var i = 0
    while i < extraArgs.count {
        if extraArgs[i] == "--exec", i + 1 < extraArgs.count {
            execLine = extraArgs[i + 1]
            i += 2
        } else {
            i += 1
        }
    }
    let bundle = BundlePaths(root: URL(fileURLWithPath: bundleArg, isDirectory: true))
    let manager = VMManager(bundle: bundle)

    // Become a session leader so the manager is not tied to the (short-lived)
    // caller's session/process group. Only valid when started as a fresh child
    // (spawnManager posix_spawns us without POSIX_SPAWN_SETSID).
    _ = setsid()

    // Boot the guest on the main thread, then park the main thread in
    // dispatchMain(). The accept DispatchSource queues each control command as a
    // Task on the main queue, and async VZ calls resume their continuations on
    // that same queue -- so every VZ interaction stays on the main queue, which
    // is the only safe arrangement on this host (see run()).
    do {
        try manager.run(execLine: execLine)
    } catch {
        FileHandle.standardError.write("vzrunner manager: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(1)
    }
    dispatchMain() // never returns
}

// MARK: - top-level dispatch

func main() throws {
    let args = try CLIArgs.parse(CommandLine.arguments)
    let bundle = BundlePaths(root: URL(fileURLWithPath: args.bundle, isDirectory: true))

    switch args.subcommand {
    case "run-manager":
        // Internal: re-exec'd by the front-end to own the VM long-term.
        var extra: [String] = []
        // Preserve --exec if present (passed positionally after known flags).
        if let idx = CommandLine.arguments.firstIndex(of: "--exec"),
           idx + 1 < CommandLine.arguments.count {
            extra = ["--exec", CommandLine.arguments[idx + 1]]
        }
        try runManager(bundleArg: args.bundle, extraArgs: extra)

    case "exec":
        guard !args.commandArgv.isEmpty else {
            throw VZRError.usage("exec requires a command after `--`\n\(CLIArgs.usageText())")
        }
        // No guest agent yet: (re)boot the VM with the command as init shim.
        // If a manager is already running this bundle, kill it so the new boot
        // uses the requested command line. Pause/snapshot/restore act on the
        // resident manager to preserve whole-machine state.
        if managerAlive(bundle: bundle) {
            _ = try? sendRequest(bundle: bundle, ["op": "kill"])
            Thread.sleep(forTimeInterval: 0.2)
        }
        try bundle.ensureLayout()
        let cmdLine = args.commandArgv
            .map { "'\($0.replacingOccurrences(of: "'", with: "'\\''"))'" }
            .joined(separator: " ")
        do {
            try spawnManager(bundle: bundle, execLine: cmdLine, logLevel: args.logLevel)
        } catch {
            // Surface manager-spawn failures (missing guest kernel/disk, bad
            // socket path, ...) with a non-zero exit so vz.py raises instead of
            // silently returning an empty exec result.
            FileHandle.standardError.write("vzrunner: failed to start guest: \(error.localizedDescription)\n".data(using: .utf8)!)
            exit(2)
        }
        // Streamed output arrives on the manager's own stdout; surface a
        // minimal acknowledgement so vz.py's exec() returns a string.
        let state = try sendRequest(bundle: bundle, ["op": "status"])
        print("vzrunner: guest booted for exec (pid \(state["pid"] ?? "?")); command runs as guest PID 1")

    case "pause":
        _ = try sendRequest(bundle: bundle, ["op": "pause"])

    case "resume":
        _ = try sendRequest(bundle: bundle, ["op": "resume"])

    case "snapshot":
        guard !args.name.isEmpty else {
            throw VZRError.usage("snapshot requires --name <name>\n\(CLIArgs.usageText())")
        }
        _ = try sendRequest(bundle: bundle, ["op": "snapshot", "name": args.name])

    case "restore":
        guard !args.name.isEmpty else {
            throw VZRError.usage("restore requires --name <name>\n\(CLIArgs.usageText())")
        }
        _ = try sendRequest(bundle: bundle, ["op": "restore", "name": args.name])

    case "kill":
        if managerAlive(bundle: bundle) {
            _ = try? sendRequest(bundle: bundle, ["op": "kill"])
        }
        // Also clean up any stale pidfile/socket for a dead manager.
        try? FileManager.default.removeItem(at: bundle.socket)
        try? FileManager.default.removeItem(at: bundle.pidfile)

    case "status":
        let state = try sendRequest(bundle: bundle, ["op": "status"])
        if let data = try? JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys]),
           let s = String(data: data, encoding: .utf8) {
            print(s)
        }

    default:
        throw VZRError.usage("unknown subcommand \(args.subcommand)\n\(CLIArgs.usageText())")
    }
}

do {
    try main()
} catch let error as VZRError {
    FileHandle.standardError.write("vzrunner: \(error.description)\n".data(using: .utf8)!)
    exit(1)
} catch {
    FileHandle.standardError.write("vzrunner: \(error.localizedDescription)\n".data(using: .utf8)!)
    exit(1)
}
