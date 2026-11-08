#!/usr/bin/env bash
set -e

echo "[TeByPass Gateway] Initializing L3 Split-Routing Sidecar..."

# 1. Enable kernel IP forwarding
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true

# 2. Load base nftables ruleset
if [ -f "/etc/nftables/tebypass.conf" ]; then
    echo "[TeByPass Gateway] Applying nftables configuration..."
    nft -f /etc/nftables/tebypass.conf || true
fi

# 3. Bring up WireGuard interface if configuration is present
if [ -f "/etc/wireguard/wg0.conf" ]; then
    echo "[TeByPass Gateway] Starting WireGuard tunnel (wg0)..."
    wg-quick up wg0 || true

    # Configure policy routing table 100 for marked packets
    ip rule add fwmark 0x100 table 100 2>/dev/null || true
    ip route add default dev wg0 table 100 2>/dev/null || true
    echo "[TeByPass Gateway] Policy routing table 100 configured for fwmark 0x100 -> wg0"
else
    echo "[TeByPass Gateway] Notice: /etc/wireguard/wg0.conf not mounted. Running in pass-through mode."
fi

# 4. Graceful termination handler
trap "echo '[TeByPass Gateway] Shutting down...'; [ -f /etc/wireguard/wg0.conf ] && wg-quick down wg0 || true; exit 0" SIGINT SIGTERM

echo "[TeByPass Gateway] Gateway sidecar ready and listening."

# Keep container running in foreground
while true; do
    sleep 3600 &
    wait $!
done
