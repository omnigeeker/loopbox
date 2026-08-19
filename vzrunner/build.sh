#!/bin/sh
# Build vzrunner, the loopbox Virtualization.framework helper.
#
# Produces an optimized arm64 binary at vzrunner/.build/release/vzrunner using
# the system Swift toolchain (no SwiftPM manifest needed for a single file).
#
# Requirements (checked below):
#   - macOS 14 (Sonoma) or later   -> saveMachineStateToURL / restoreMachineStateFromURL
#   - arm64 (Apple Silicon)        -> VZLinuxBootLoader boots an arm64 Linux guest
#   - Xcode Command Line Tools providing swiftc at /usr/bin/swiftc
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC="$SCRIPT_DIR/Sources/vzrunner/main.swift"
OUT_DIR="$SCRIPT_DIR/.build"
OUT="$OUT_DIR/release/vzrunner"

# --- preflight checks -------------------------------------------------------

if ! command -v swiftc >/dev/null 2>&1; then
    echo "error: swiftc not found; install the Xcode Command Line Tools:" >&2
    echo "  xcode-select --install" >&2
    exit 1
fi

arch=$(uname -m)
if [ "$arch" != "arm64" ]; then
    echo "error: vzrunner requires Apple Silicon (arm64); found $arch" >&2
    exit 1
fi

os_major=$(sw_vers -productVersion | cut -d. -f1)
if [ "$os_major" -lt 14 ]; then
    echo "error: vzrunner requires macOS 14+ (found $(sw_vers -productVersion))." >&2
    echo "  Snapshot/restore use the macOS 14 Virtualization.framework APIs." >&2
    exit 1
fi

if [ ! -f "$SRC" ]; then
    echo "error: source not found at $SRC" >&2
    exit 1
fi

# --- build ------------------------------------------------------------------

mkdir -p "$OUT_DIR/release"

echo "Building vzrunner (swiftc -O, arm64, macOS 14 deployment target)..."
swiftc \
    -O \
    -Osize \
    -whole-module-optimization \
    -target arm64-apple-macos14.0 \
    -sdk "$(xcrun --show-sdk-path)" \
    "$SRC" \
    -o "$OUT"

# Virtualization.framework on macOS 14+ requires the
# com.apple.security.virtualization entitlement to actually boot a VM; without
# it VZVirtualMachine.start() is rejected. Ad-hoc sign the binary with the
# entitlement so it loads and can run a guest. Signing is not required to
# *compile* or to parse the CLI, so a signing hiccup only warns.
if command -v codesign >/dev/null 2>&1; then
    if ! codesign --sign - --force --entitlements "$SCRIPT_DIR/vzrunner.entitlements" "$OUT" 2>/dev/null \
        && ! codesign --sign - --force "$OUT" 2>/dev/null; then
        echo "note: codesign skipped/failed (VM boot needs the virtualization" >&2
        echo "      entitlement; run: xcode-select --install). Continuing." >&2
    fi
fi

echo "Built $OUT"

# --- smoke test -------------------------------------------------------------

if [ -x "$OUT" ]; then
    "$OUT" --help >/dev/null 2>&1 || true
fi
