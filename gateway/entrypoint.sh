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

# 1. Enable kernel IP forwarding
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true

# 2. Load base nftables ruleset
if [ -f "/etc/nftables/tebypass.conf" ]; then
    echo "[TeByPass Gateway] Applying nftables configuration..."
    nft -f /etc/nftables/tebypass.conf || true
fi

# 3. Bring up WireGuard interface if configuration is present
if [ -n "$WG_CONF" ]; then
    echo "[TeByPass Gateway] Starting WireGuard tunnel from $WG_CONF ..."
    install -d -m 700 /etc/wireguard
    cp "$WG_CONF" /etc/wireguard/wg0.conf
    # Enforce split-routing: ensure Table = off so wg-quick doesn't alter the default route
    # or set sysctl src_valid_mark (which fails on unprivileged container runtimes)
    if ! grep -q 'Table' /etc/wireguard/wg0.conf; then
        sed -i '/\[Interface\]/a Table = off' /etc/wireguard/wg0.conf
    fi
    # Strip DNS to avoid resolvconf failures inside container
    sed -i '/^[[:space:]]*DNS[[:space:]]*=/d' /etc/wireguard/wg0.conf
    chmod 600 /etc/wireguard/wg0.conf

    wg-quick up wg0 || true

    # Configure policy routing table 100 for marked packets
    ip rule add fwmark 0x100 table 100 2>/dev/null || true
    ip route add default dev wg0 table 100 2>/dev/null || true
    echo "[TeByPass Gateway] Policy routing table 100 configured for fwmark 0x100 -> wg0"
else
    echo "[TeByPass Gateway] Notice: no wg0.conf found. Upload it from the web UI. Running in pass-through mode."
fi

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
wait "$CONTROLLER_PID" || true
exit 0
