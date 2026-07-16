#!/bin/bash

set -e -u -o pipefail

# shellcheck source=/scripts/utils.sh
source /scripts/utils.sh
# shellcheck source=/scripts/privado.sh
source /scripts/privado.sh

DATA_DIR=/tmp/privado-cleanup-test
NETWORK_STATE_FILE="${DATA_DIR}/original-default-route"
mkdir -p "${DATA_DIR}"

default_route=$(ip -4 route show default | head -1)
recovery_route=$(route_gateway_and_interface "${default_route}")
read -r original_gateway original_interface <<< "${recovery_route}"
printf '%s %s\n' "${original_gateway}" "${original_interface}" > "${NETWORK_STATE_FILE}"

ip link add wg0 type dummy
ip address add 10.0.0.2/32 dev wg0
ip link set wg0 up
ip -4 route add default table 51820 dev wg0
ip -4 rule add table main suppress_prefixlength 0 priority 32764
ip -4 rule add not fwmark 51820 table 51820 priority 32765
iptables -I OUTPUT ! -o wg0 -m mark ! --mark 51820 \
  -m addrtype ! --dst-type LOCAL -j REJECT
iptables -t raw -I PREROUTING ! -i wg0 -d 10.0.0.2/32 \
  -m addrtype ! --src-type LOCAL -j DROP
iptables -t mangle -I POSTROUTING -m mark --mark 51820 \
  -p udp -j CONNMARK --save-mark
iptables -t mangle -I PREROUTING -p udp -m conntrack \
  --ctstate RELATED,ESTABLISHED -j CONNMARK --restore-mark
ip -4 route replace default dev wg0

cleanup_wireguard_state

ip -4 route show default | grep -Fq "via ${original_gateway} dev ${original_interface}"
if ip link show wg0 >/dev/null 2>&1; then
  echo "wg0 still exists after cleanup" >&2
  exit 1
fi
if ip -4 rule show | grep -Eq '51820|suppress_prefixlength'; then
  echo "WireGuard policy rules still exist after cleanup" >&2
  exit 1
fi
if iptables -S OUTPUT | grep -Fq 'wg0'; then
  echo "WireGuard OUTPUT rule still exists after cleanup" >&2
  exit 1
fi

echo "OK: stale WireGuard routes, rules, interface, and firewall state were removed"
