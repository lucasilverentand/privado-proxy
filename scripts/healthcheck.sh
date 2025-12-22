#!/bin/bash

set -e -u -o pipefail

# Check if wg0 interface exists
if ! ip link show wg0 &>/dev/null; then
  echo "ERROR: WireGuard interface wg0 not found"
  exit 1
fi

# Check if microsocks is running
if ! pgrep -x microsocks >/dev/null; then
  echo "ERROR: microsocks process not running"
  exit 1
fi

# Verify WireGuard handshake is recent (within 3 minutes)
latest_handshake=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}')

if [[ -z "${latest_handshake}" ]] || [[ "${latest_handshake}" == "0" ]]; then
  echo "WARNING: No WireGuard handshake detected"
  # Try to restart the interface
  wg-quick down wg0 2>/dev/null || true
  wg-quick up wg0
  sleep 5

  # Check again
  latest_handshake=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}')
  if [[ -z "${latest_handshake}" ]] || [[ "${latest_handshake}" == "0" ]]; then
    echo "ERROR: Failed to establish WireGuard connection"
    exit 1
  fi
fi

now=$(date +%s)
age=$((now - latest_handshake))

if [[ ${age} -gt 180 ]]; then
  echo "WARNING: WireGuard handshake is ${age}s old, attempting reconnect"
  wg-quick down wg0 2>/dev/null || true
  wg-quick up wg0
  sleep 5

  # Verify reconnection
  latest_handshake=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}')
  if [[ -z "${latest_handshake}" ]] || [[ "${latest_handshake}" == "0" ]]; then
    echo "ERROR: Reconnection failed"
    exit 1
  fi
fi

echo "OK: WireGuard connected, handshake ${age}s ago"
exit 0
