FROM alpine:3.20

LABEL maintainer="TeByPass Contributors"
LABEL description="Alpine Linux L3 Split-Routing Gateway Sidecar with nftables and WireGuard"

RUN apk add --no-cache \
    nftables \
    iproute2 \
    wireguard-tools \
    iptables \
    python3 \
    bash \
    ca-certificates \
    tzdata \
    curl

COPY entrypoint.sh /entrypoint.sh
COPY controller.py /controller.py
COPY nftables.conf /etc/nftables/tebypass.conf

RUN chmod +x /entrypoint.sh /controller.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD nft list table inet tebypass > /dev/null && test -S "${TEBYPASS_GATEWAY_SOCKET:-/data/gateway.sock}" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
