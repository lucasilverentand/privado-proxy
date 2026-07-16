#!/bin/bash

enforce_proxies_iptables() {
  log "INFO: Configuring iptables for proxy ports"

  # Allow incoming connections on SOCKS5 port
  iptables -C INPUT -p tcp --dport "${SOCK_PORT}" -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport "${SOCK_PORT}" -j ACCEPT 2>/dev/null || true
  iptables -C INPUT -p udp --dport "${SOCK_PORT}" -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p udp --dport "${SOCK_PORT}" -j ACCEPT 2>/dev/null || true

  if [[ "${DASHBOARD_ENABLED,,}" == "true" ]]; then
    iptables -C INPUT -p tcp --dport "${DASHBOARD_PORT}" -j ACCEPT 2>/dev/null || \
      iptables -A INPUT -p tcp --dport "${DASHBOARD_PORT}" -j ACCEPT 2>/dev/null || true
  fi

  # Allow established connections
  iptables -C INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -C OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
    iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true

  log "INFO: iptables configured"
}

setup_dns() {
  log "INFO: Setting up DNS"

  # Use custom DNS if provided, otherwise use defaults
  local dns_servers="${DNS:-9.9.9.9,149.112.112.112}"

  # Clear existing resolv.conf
  echo "" > /etc/resolv.conf

  # Add each DNS server
  for dns in ${dns_servers//,/ }; do
    echo "nameserver ${dns}" >> /etc/resolv.conf
  done

  log "INFO: DNS configured: ${dns_servers}"
}
