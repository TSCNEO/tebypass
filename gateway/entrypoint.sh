#!/bin/bash
set -e

echo "[TeByPass Gateway] Initializing L3 Split-Routing Sidecar..."

CONTROLLER_BIN="${TEBYPASS_CONTROLLER_BIN:-/controller.py}"
CONTROLLER_PID=""

# WireGuard configuration is uploaded from the web UI into the shared data volume.
# Fall back to the legacy bind mount for backwards compatibility.
WG_DIR="${TEBYPASS_WG_DIR:-/data/wireguard}"
WG_CONF=""
if [ -f "$WG_DIR/wg0.conf" ]; then
    WG_CONF="$WG_DIR/wg0.conf"
elif [ -f "/etc/wireguard/wg0.conf" ]; then
    WG_CONF="/etc/wireguard/wg0.conf"
fi

terminate() {
    trap - SIGINT SIGTERM
    echo "[TeByPass Gateway] Shutting down..."
    if [ -n "$CONTROLLER_PID" ] && kill -0 "$CONTROLLER_PID" 2>/dev/null; then
        kill -TERM "$CONTROLLER_PID" 2>/dev/null || true
        wait "$CONTROLLER_PID" 2>/dev/null || true
    fi
    if [ -f /etc/wireguard/wg0.conf ]; then
        wg-quick down wg0 >/dev/null 2>&1 || true
    fi
    exit 0
}
trap terminate SIGINT SIGTERM

# Kernel forwarding and base rules must be valid before accepting control commands.
if [ "$(cat /proc/sys/net/ipv4/ip_forward)" != "1" ]; then
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
fi
nft -f /etc/nftables/tebypass.conf
# The controller installs fail-closed policy routing and owns tunnel reloads.

# 4. Start the Unix domain socket control daemon shared with the application
if [ -f "$CONTROLLER_BIN" ]; then
    echo "[TeByPass Gateway] Starting control daemon on ${TEBYPASS_GATEWAY_SOCKET:-/data/gateway.sock} ..."
    python3 "$CONTROLLER_BIN" &
    CONTROLLER_PID=$!
else
    echo "[TeByPass Gateway] ERROR: control daemon not found at $CONTROLLER_BIN" >&2
    exit 1
fi

echo "[TeByPass Gateway] Gateway sidecar ready and listening."

# Stay in the foreground on the daemon so container lifecycle maps to its lifecycle.
wait "$CONTROLLER_PID"
