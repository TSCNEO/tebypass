FROM alpine:3.20

LABEL maintainer="TeByPass Contributors"
LABEL description="Alpine Linux L3 Split-Routing Gateway Sidecar with nftables and WireGuard"

RUN apk add --no-cache \
    nftables \
    iproute2 \
    wireguard-tools \
    iptables \
    bash \
    ca-certificates \
    tzdata

COPY entrypoint.sh /entrypoint.sh
COPY nftables.conf /etc/nftables/tebypass.conf

RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD nft list table inet tebypass > /dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
