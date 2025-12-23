#!/bin/bash

set -u -o pipefail

source /scripts/vars.sh
source /scripts/utils.sh
source /scripts/iptables.sh
source /scripts/privado.sh
source /scripts/dante.sh

print_settings
set_timezone

# Set sysctl for WireGuard policy-based routing
log "INFO: Setting net.ipv4.conf.all.src_valid_mark=1 for WireGuard"
if ! error_msg=$(sysctl -w net.ipv4.conf.all.src_valid_mark=1 2>&1); then
  log "WARNING: Failed to set src_valid_mark sysctl: ${error_msg}"
  log "WARNING: WireGuard policy-based routing may not work correctly"
fi

# Validate required parameters
if [[ -z ${PRIVADO_USERNAME} ]] || [[ -z ${PRIVADO_PASSWORD} ]] || [[ -z ${PRIVADO_SERVER} ]]; then
  log "ERROR: PRIVADO_USERNAME, PRIVADO_PASSWORD, and PRIVADO_SERVER are required"
  log "ERROR: Set these via environment variables or Docker secrets"
  exit 1
fi

# Setup Privado VPN via WireGuard
login_privado
get_servers
get_wireguard_config
setup_wireguard
connect_privado
check_connection

# Setup iptables and DNS
enforce_proxies_iptables
setup_dns

# Log public IP for verification
PUBLIC_IP=$(get_public_ip)
log "INFO: Public IP: ${PUBLIC_IP}"

# Start Dante SOCKS5 proxy
setup_dante
start_dante

log "INFO: Privado VPN Proxy is ready"
log "INFO: SOCKS5 proxy available on port ${SOCK_PORT}"
