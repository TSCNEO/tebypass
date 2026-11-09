#!/usr/bin/env bash
set -e

echo "[TeByPass Gateway] Initializing L3 Split-Routing Sidecar..."

# WireGuard configuration is uploaded from the web UI into the shared data volume.
# Fall back to the legacy bind mount for backwards compatibility.
WG_DIR="${TEBYPASS_WG_DIR:-/data/wireguard}"
WG_CONF=""
if [ -f "$WG_DIR/wg0.conf" ]; then
    WG_CONF="$WG_DIR/wg0.conf"
elif [ -f "/etc/wireguard/wg0.conf" ]; then
    WG_CONF="/etc/wireguard/wg0.conf"
fi

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
    # Enforce split-routing: ensure Table = off so wg-quick doesn't alter default route
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

# 4. Graceful termination handler
trap "echo '[TeByPass Gateway] Shutting down...'; [ -f /etc/wireguard/wg0.conf ] && wg-quick down wg0 || true; exit 0" SIGINT SIGTERM

echo "[TeByPass Gateway] Gateway sidecar ready and listening."

# Keep container running in foreground
while true; do
    sleep 3600 &
    wait $!
done
