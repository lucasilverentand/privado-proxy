FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install dependencies including WireGuard and microsocks
RUN apt-get update && apt-get install -y \
    curl jq microsocks supervisor iproute2 iptables \
    net-tools gettext wireguard-tools procps \
    && apt-get clean all && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create user and group
RUN addgroup --system vpn && useradd -lNms /bin/bash -u "${NUID:-1000}" -G vpn privadoclient

# Install scripts and make them executable
COPY scripts/ /scripts/
RUN chmod +x /scripts/*.sh

COPY etc/ /etc/

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
HEALTHCHECK --interval=5m --timeout=1m --start-period=1m CMD /scripts/healthcheck.sh
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
