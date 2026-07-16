#!/bin/bash

set -e -u -o pipefail

# VARS_FILE is overridable for local tests; containers use the installed file.
# shellcheck source=/scripts/vars.sh
source "${VARS_FILE:-/scripts/vars.sh}"

request_recovery() {
  if [[ -z "${PRIVADO_USERNAME}" ]] || [[ -z "${PRIVADO_PASSWORD}" ]]; then
    return 0
  fi

  local main_status
  main_status=$(supervisorctl status main 2>/dev/null || true)
  if [[ "${main_status}" == *"RUNNING"* ]] || [[ "${main_status}" == *"STARTING"* ]]; then
    return 0
  fi

  if supervisorctl start main >/dev/null 2>&1; then
    echo "INFO: Requested a clean VPN reconnect through supervisor"
  else
    echo "WARNING: Could not request a VPN reconnect through supervisor"
  fi
}

fail_healthcheck() {
  echo "ERROR: $*"
  request_recovery
  exit 1
}

if ! ip link show wg0 &>/dev/null; then
  fail_healthcheck "WireGuard interface wg0 not found"
fi

if ! pgrep -x microsocks >/dev/null; then
  fail_healthcheck "microsocks process not running"
fi

latest_handshake=$(wg show wg0 latest-handshakes 2>/dev/null | \
  awk '$2 > latest { latest = $2 } END { print latest }' || true)

if [[ -z "${latest_handshake}" ]] || [[ "${latest_handshake}" == "0" ]]; then
  fail_healthcheck "No WireGuard handshake detected"
fi

now=$(date +%s)
age=$((now - latest_handshake))

if [[ ${age} -gt 180 ]]; then
  fail_healthcheck "WireGuard handshake is ${age}s old"
fi

# --socks5-hostname makes the proxy resolve the hostname through the tunnel.
# The HTTPS request also proves that the image contains a usable CA bundle.
if ! curl --fail --silent --show-error \
  --max-time "${HEALTHCHECK_TIMEOUT}" \
  --socks5-hostname "127.0.0.1:${SOCK_PORT}" \
  --output /dev/null \
  "${HEALTHCHECK_URL}"; then
  fail_healthcheck "SOCKS hostname request failed for ${HEALTHCHECK_URL}"
fi

echo "OK: WireGuard connected, handshake ${age}s ago; SOCKS DNS and HTTPS succeeded"
