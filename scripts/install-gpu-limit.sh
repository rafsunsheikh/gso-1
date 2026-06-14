#!/bin/bash
# Persist the GPU (Metal) wired-memory limit across reboots so the local LLM
# has room for a large KV cache. Run with: sudo bash scripts/install-gpu-limit.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/com.local.iogpu.wiredlimit.plist"
DST=/Library/LaunchDaemons/com.local.iogpu.wiredlimit.plist

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root:  sudo bash scripts/install-gpu-limit.sh" >&2
    exit 1
fi

cp "$SRC" "$DST"
chown root:wheel "$DST"
chmod 644 "$DST"

# (Re)load the daemon and apply immediately.
launchctl bootout system "$DST" 2>/dev/null || true
launchctl bootstrap system "$DST"

echo "Installed. Current value:"
sysctl iogpu.wired_limit_mb
