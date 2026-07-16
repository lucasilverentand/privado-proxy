#!/bin/bash

# Privado VPN API endpoints
CLIENT_API_URL="https://client-api.privado.io/v1"
API_KEY="9f994c466340e8f2ed60a99396fecb6a"
USER_AGENT="App: 3.0.0 (576942783), macOS: Version 12.4 (Build 21F79)"

# Data directory for storing session data
DATA_DIR=${DATA_DIR:-"/run/privado"}
TOKEN_FILE="${DATA_DIR}/token.json"
SERVERS_FILE="${DATA_DIR}/servers.json"
NETWORK_STATE_FILE="${DATA_DIR}/original-default-route"
WG_CONFIG=${WG_CONFIG:-"/etc/wireguard/wg0.conf"}

# Initialize data directory
init_privado() {
  mkdir -p "${DATA_DIR}"
  mkdir -p /etc/wireguard
}

# Extract the gateway and interface from an iproute2 route line.
route_gateway_and_interface() {
  local route="${1}"
  local gateway=""
  local interface=""
  local -a fields

  read -r -a fields <<< "${route}"
  for ((index = 0; index < ${#fields[@]}; index++)); do
    case "${fields[index]}" in
      via)
        gateway="${fields[index + 1]:-}"
        ;;
      dev)
        interface="${fields[index + 1]:-}"
        ;;
    esac
  done

  if [[ -n "${gateway}" ]] && [[ -n "${interface}" ]] && [[ "${interface}" != "wg0" ]]; then
    printf '%s %s\n' "${gateway}" "${interface}"
  fi
}

# Recover the pre-tunnel route from persisted state or the endpoint route that
# survives when a container restarts inside the same network namespace.
find_original_default_route() {
  local gateway=""
  local interface=""
  local route=""
  local endpoint=""
  local endpoint_ip=""

  if [[ -s "${NETWORK_STATE_FILE}" ]]; then
    read -r gateway interface < "${NETWORK_STATE_FILE}" || true
    if [[ -n "${gateway}" ]] && [[ -n "${interface}" ]] && [[ "${interface}" != "wg0" ]]; then
      printf '%s %s\n' "${gateway}" "${interface}"
      return 0
    fi
  fi

  route=$(ip -4 route show default 2>/dev/null | awk '$0 !~ / dev wg0( |$)/ { print; exit }')
  if [[ -n "${route}" ]]; then
    route_gateway_and_interface "${route}"
    return 0
  fi

  endpoint=$(wg show wg0 endpoints 2>/dev/null | awk 'NR == 1 { print $2 }' || true)
  endpoint_ip="${endpoint%:*}"
  if [[ "${endpoint_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    route=$(ip -4 route show table main "${endpoint_ip}/32" 2>/dev/null | head -1)
  fi

  if [[ -z "${route}" ]]; then
    route=$(ip -4 route show table main 2>/dev/null | awk '
      / via / && $0 !~ / dev wg0( |$)/ { print; exit }
    ')
  fi

  route_gateway_and_interface "${route}"
}

# Remove every piece of WireGuard state that can outlive main.sh. This handles
# both the current direct-default-route setup and legacy wg-quick fwmark rules.
cleanup_wireguard_state() {
  log "INFO: PRIVADO: Cleaning up stale WireGuard state"

  local current_default=""
  local recovery_route=""
  local gateway=""
  local interface=""
  local endpoint=""
  local endpoint_ip=""
  local interface_address=""
  local fwmark=""
  local mark=""
  local -a marks=("51820")

  current_default=$(ip -4 route show default 2>/dev/null | head -1)
  recovery_route=$(find_original_default_route || true)
  read -r gateway interface <<< "${recovery_route}"

  endpoint=$(wg show wg0 endpoints 2>/dev/null | awk 'NR == 1 { print $2 }' || true)
  endpoint_ip="${endpoint%:*}"
  interface_address=$(ip -o -4 addr show dev wg0 2>/dev/null | awk 'NR == 1 { print $4 }' || true)
  fwmark=$(wg show wg0 fwmark 2>/dev/null || true)
  if [[ -n "${fwmark}" ]] && [[ "${fwmark}" != "off" ]] && [[ "${fwmark}" != "51820" ]]; then
    marks+=("${fwmark}")
  fi

  # Let wg-quick remove everything it recognizes, then explicitly remove
  # partial state in case its config file is absent or incomplete.
  wg-quick down wg0 >/dev/null 2>&1 || true

  for mark in "${marks[@]}"; do
    while ip -4 rule del not fwmark "${mark}" table "${mark}" 2>/dev/null; do :; done
    ip -4 route flush table "${mark}" 2>/dev/null || true

    while iptables -D OUTPUT ! -o wg0 -m mark ! --mark "${mark}" \
      -m addrtype ! --dst-type LOCAL -j REJECT 2>/dev/null; do :; done
    while iptables -t mangle -D POSTROUTING -m mark --mark "${mark}" \
      -p udp -j CONNMARK --save-mark 2>/dev/null; do :; done
  done

  while ip -4 rule del table main suppress_prefixlength 0 2>/dev/null; do :; done
  while iptables -t mangle -D PREROUTING -p udp -m conntrack \
    --ctstate RELATED,ESTABLISHED -j CONNMARK --restore-mark 2>/dev/null; do :; done

  if [[ -n "${interface_address}" ]]; then
    while iptables -t raw -D PREROUTING ! -i wg0 -d "${interface_address}" \
      -m addrtype ! --src-type LOCAL -j DROP 2>/dev/null; do :; done
  fi

  ip link del wg0 2>/dev/null || true

  if [[ -z "${current_default}" ]] || [[ "${current_default}" == *" dev wg0"* ]]; then
    if [[ -n "${gateway}" ]] && [[ -n "${interface}" ]]; then
      if ip -4 route replace default via "${gateway}" dev "${interface}"; then
        log "INFO: PRIVADO: Restored default route via ${gateway} dev ${interface}"
      else
        log "WARNING: PRIVADO: Failed to restore default route via ${gateway} dev ${interface}"
      fi
    else
      log "WARNING: PRIVADO: No original default route could be recovered"
    fi
  fi

  if [[ "${endpoint_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ip -4 route del "${endpoint_ip}/32" 2>/dev/null || true
  fi

  rm -f "${NETWORK_STATE_FILE}"
  return 0
}

# Login to Privado API and get access token
login_privado() {
  log "INFO: PRIVADO: Logging in to Privado API"

  init_privado

  local response
  response=$(curl -sS --compressed -X POST "${CLIENT_API_URL}/login" \
    -H "Content-Type: application/json" \
    -H "User-Agent: ${USER_AGENT}" \
    -d "{\"api_key\":\"${API_KEY}\",\"username\":\"${PRIVADO_USERNAME}\",\"password\":\"${PRIVADO_PASSWORD}\"}")

  if [[ -z "${response}" ]]; then
    log "ERROR: PRIVADO: Failed to get response from API"
    exit 1
  fi

  # Check for error in response
  local error
  error=$(echo "${response}" | jq -r '.error // empty')
  if [[ -n "${error}" ]]; then
    log "ERROR: PRIVADO: Login failed - ${error}"
    exit 1
  fi

  # Save token
  echo "${response}" > "${TOKEN_FILE}"

  # Extract and verify access token
  ACCESS_TOKEN=$(jq -r '.access_token // .token // empty' "${TOKEN_FILE}")
  if [[ -z "${ACCESS_TOKEN}" ]]; then
    log "ERROR: PRIVADO: No access token in response"
    log "DEBUG: Response: ${response}"
    exit 1
  fi

  log "INFO: PRIVADO: Successfully logged in"
}

# Get list of available servers
get_servers() {
  log "INFO: PRIVADO: Fetching server list"

  ACCESS_TOKEN=$(jq -r '.access_token // .token // empty' "${TOKEN_FILE}")

  local response
  response=$(curl -sS --compressed -X GET "${CLIENT_API_URL}/servers?nodes=all&includegeo=1" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "User-Agent: ${USER_AGENT}")

  if [[ -z "${response}" ]]; then
    log "ERROR: PRIVADO: Failed to get server list"
    exit 1
  fi

  # Check if response has a 'servers' wrapper or is direct array
  if echo "${response}" | jq -e '.servers' > /dev/null 2>&1; then
    # Extract servers array from wrapper
    echo "${response}" | jq '.servers' > "${SERVERS_FILE}"
  else
    # Direct array or other format
    echo "${response}" > "${SERVERS_FILE}"
  fi

  log "INFO: PRIVADO: Server list retrieved"
}

# Find server by name, city, or country
find_server() {
  local query="${1}"
  local server_name

  # Convert query to lowercase for comparison
  local query_lower
  query_lower=$(echo "${query}" | tr '[:upper:]' '[:lower:]')

  # Try to find by exact server name first (excluding maintenance servers)
  server_name=$(jq -r --arg query "${query}" '.[] | select(.maintenance == false) | select(.name == $query) | .name' "${SERVERS_FILE}" | head -1)

  # If not found, check if query is in country-city format (e.g., "nl-ams", "netherlands-amsterdam")
  if [[ -z "${server_name}" ]] && [[ "${query}" == *-* ]]; then
    local country_part
    local city_part
    country_part=$(echo "${query}" | cut -d'-' -f1 | tr '[:upper:]' '[:lower:]')
    city_part=$(echo "${query}" | cut -d'-' -f2- | tr '[:upper:]' '[:lower:]')
    
    # Only proceed if both parts are non-empty
    if [[ -n "${country_part}" ]] && [[ -n "${city_part}" ]]; then
      # Try to find server matching both country (partial) and city (partial) in respective fields
      server_name=$(jq -r --arg country "${country_part}" --arg city "${city_part}" '.[] | select(.maintenance == false) | select((.country | ascii_downcase | contains($country)) and (.city | ascii_downcase | contains($city))) | .name' "${SERVERS_FILE}" | head -1)
      
      # If still not found, try flexible matching where city_part matches server name start 
      # and country_part matches country field (useful for codes like "nl-ams" where server is "ams-001...")
      if [[ -z "${server_name}" ]]; then
        server_name=$(jq -r --arg country "${country_part}" --arg city "${city_part}" '.[] | select(.maintenance == false) | select((.country | ascii_downcase | contains($country)) and (.name | ascii_downcase | startswith($city))) | .name' "${SERVERS_FILE}" | head -1)
      fi
      
      # If still not found with country constraint, try just city_part at start of server name
      # This handles cases where country code (e.g., "nl") doesn't match the full country name (e.g., "Netherlands")
      if [[ -z "${server_name}" ]]; then
        server_name=$(jq -r --arg city "${city_part}" '.[] | select(.maintenance == false) | select(.name | ascii_downcase | startswith($city)) | .name' "${SERVERS_FILE}" | head -1)
      fi
    fi
  fi

  # If not found, try by country (case insensitive partial match)
  if [[ -z "${server_name}" ]]; then
    server_name=$(jq -r --arg query "${query_lower}" '.[] | select(.maintenance == false) | select(.country | ascii_downcase | contains($query)) | .name' "${SERVERS_FILE}" | head -1)
  fi

  # If still not found, try by city (case insensitive partial match)
  if [[ -z "${server_name}" ]]; then
    server_name=$(jq -r --arg query "${query_lower}" '.[] | select(.maintenance == false) | select(.city | ascii_downcase | contains($query)) | .name' "${SERVERS_FILE}" | head -1)
  fi

  # If still not found, try partial name match
  if [[ -z "${server_name}" ]]; then
    server_name=$(jq -r --arg query "${query_lower}" '.[] | select(.maintenance == false) | select(.name | ascii_downcase | contains($query)) | .name' "${SERVERS_FILE}" | head -1)
  fi

  if [[ -z "${server_name}" ]]; then
    log "ERROR: PRIVADO: Could not find server matching '${query}'"
    log "INFO: Available countries:"
    jq -r '.[] | select(.maintenance == false) | .country' "${SERVERS_FILE}" | sort -u | tr '\n' ', '
    echo ""
    exit 1
  fi

  echo "${server_name}"
}

# Select a configured server, or automatically choose the first available server.
select_server() {
  if [[ -n "${PRIVADO_SERVER}" ]]; then
    find_server "${PRIVADO_SERVER}"
    return
  fi

  local server_name
  server_name=$(jq -r '.[] | select(.maintenance == false) | .name' "${SERVERS_FILE}" | head -1)

  if [[ -z "${server_name}" ]] || [[ "${server_name}" == "null" ]]; then
    log "ERROR: PRIVADO: Could not auto-select an available server"
    exit 1
  fi

  PRIVADO_SERVER="${server_name}"
  log "INFO: PRIVADO: No server configured; auto-selected ${server_name}"
  echo "${server_name}"
}

# Get WireGuard configuration from server
get_wireguard_config() {
  log "INFO: PRIVADO: Getting WireGuard configuration"

  # Find the server hostname
  local server_hostname
  server_hostname=$(select_server)
  log "INFO: PRIVADO: Selected server: ${server_hostname}"

  # Get WireGuard credentials from the server directly
  local response
  response=$(curl -sS --compressed -X POST "https://${server_hostname}:44121/api/1.0/login" \
    -H "Content-Type: application/json" \
    -H "User-Agent: ${USER_AGENT}" \
    -d "{\"Username\":\"${PRIVADO_USERNAME}\",\"Password\":\"${PRIVADO_PASSWORD}\"}" \
    --connect-timeout 30)

  if [[ -z "${response}" ]]; then
    log "ERROR: PRIVADO: Failed to get WireGuard config from ${server_hostname}"
    exit 1
  fi

  # Check for error
  local error
  error=$(echo "${response}" | jq -r '.error // empty')
  if [[ -n "${error}" ]]; then
    log "ERROR: PRIVADO: WireGuard config failed - ${error}"
    exit 1
  fi

  # Extract WireGuard configuration values
  WG_PRIVATE_KEY=$(echo "${response}" | jq -r '.WGPrivateKey // empty')
  WG_ADDRESS=$(echo "${response}" | jq -r '.WGIPAddress // empty')
  WG_SERVER_PUBLIC_KEY=$(echo "${response}" | jq -r '.ServerPublicKey // empty')
  WG_SERVER_IP=$(echo "${response}" | jq -r '.ServerIPAddress // empty')
  WG_SERVER_PORT=$(echo "${response}" | jq -r '.ServerListeningPort // 51820')

  # Construct endpoint from server IP and port
  WG_ENDPOINT="${WG_SERVER_IP}:${WG_SERVER_PORT}"

  if [[ -z "${WG_PRIVATE_KEY}" ]] || [[ -z "${WG_ADDRESS}" ]] || [[ -z "${WG_SERVER_PUBLIC_KEY}" ]] || [[ -z "${WG_SERVER_IP}" ]]; then
    log "ERROR: PRIVADO: Incomplete WireGuard configuration received"
    log "DEBUG: Response: ${response}"
    exit 1
  fi

  log "INFO: PRIVADO: WireGuard configuration obtained"
  log "INFO: PRIVADO: Interface IP: ${WG_ADDRESS}"

}

# Generate WireGuard configuration file
setup_wireguard() {
  log "INFO: PRIVADO: Setting up WireGuard configuration"

  # Create pure WireGuard config file (no wg-quick directives)
  cat > "${WG_CONFIG}" << EOF
[Interface]
PrivateKey = ${WG_PRIVATE_KEY}

[Peer]
PublicKey = ${WG_SERVER_PUBLIC_KEY}
AllowedIPs = 0.0.0.0/0
Endpoint = ${WG_ENDPOINT}
PersistentKeepalive = 25
EOF

  chmod 600 "${WG_CONFIG}"
  log "INFO: PRIVADO: WireGuard config written to ${WG_CONFIG}"
}

# Connect to VPN using WireGuard
connect_privado() {
  log "INFO: PRIVADO: Connecting via WireGuard"

  # Remove existing interface if present
  ip link del wg0 2>/dev/null || true

  # Get current default gateway info
  local default_route default_gw default_if
  default_route=$(ip -4 route show default | head -1)
  default_gw=$(awk '{ for (i = 1; i <= NF; i++) if ($i == "via") print $(i + 1) }' <<< "${default_route}")
  default_if=$(awk '{ for (i = 1; i <= NF; i++) if ($i == "dev") print $(i + 1) }' <<< "${default_route}")

  if [[ -z "${default_gw}" ]] || [[ -z "${default_if}" ]] || [[ "${default_if}" == "wg0" ]]; then
    log "ERROR: PRIVADO: Cannot determine the original default route"
    exit 1
  fi

  mkdir -p "${DATA_DIR}"
  printf '%s %s\n' "${default_gw}" "${default_if}" > "${NETWORK_STATE_FILE}"
  log "INFO: PRIVADO: Original gateway: ${default_gw} via ${default_if}"

  # Create WireGuard interface
  if ! ip link add wg0 type wireguard; then
    log "ERROR: PRIVADO: Failed to create WireGuard interface"
    exit 1
  fi
  if ! wg setconf wg0 "${WG_CONFIG}"; then
    log "ERROR: PRIVADO: Failed to configure WireGuard interface"
    exit 1
  fi
  if ! ip addr add "${WG_ADDRESS}/32" dev wg0; then
    log "ERROR: PRIVADO: Failed to assign WireGuard address"
    exit 1
  fi
  if ! ip link set wg0 up; then
    log "ERROR: PRIVADO: Failed to bring up WireGuard interface"
    exit 1
  fi

  # Add route to VPN server via original gateway (so we can reach it)
  if ! ip -4 route replace "${WG_SERVER_IP}/32" via "${default_gw}" dev "${default_if}"; then
    log "ERROR: PRIVADO: Failed to preserve the route to the VPN endpoint"
    exit 1
  fi

  # Add routes for local subnets to bypass VPN (required for Kubernetes cluster traffic)
  log "INFO: PRIVADO: Adding routes for local subnets"
  IFS=',' read -ra SUBNETS <<< "${LOCAL_SUBNETS}"
  for subnet in "${SUBNETS[@]}"; do
    subnet=$(echo "${subnet}" | xargs) # trim whitespace
    if [[ -n "${subnet}" ]]; then
      if ip route add "${subnet}" via "${default_gw}" dev "${default_if}" 2>/dev/null; then
        log "INFO: PRIVADO: Added route for ${subnet}"
      else
        log "DEBUG: PRIVADO: Route for ${subnet} already exists or failed"
      fi
    fi
  done

  # Add route for Docker/Kubernetes pod network if different from LOCAL_SUBNETS
  if [[ -n "${DOCKER_NET}" ]]; then
    if ip route add "${DOCKER_NET}" via "${default_gw}" dev "${default_if}" 2>/dev/null; then
      log "INFO: PRIVADO: Added route for DOCKER_NET ${DOCKER_NET}"
    else
      log "DEBUG: PRIVADO: Route for DOCKER_NET ${DOCKER_NET} already exists or failed"
    fi
  fi

  # Replace default route with WireGuard tunnel
  if ! ip -4 route replace default dev wg0; then
    log "ERROR: PRIVADO: Failed to route traffic through WireGuard"
    exit 1
  fi

  if ! ip link show wg0 up &>/dev/null; then
    log "ERROR: PRIVADO: Failed to bring up WireGuard interface"
    exit 1
  fi

  log "INFO: PRIVADO: WireGuard interface is up"
}

# Disconnect from VPN
disconnect_privado() {
  log "INFO: PRIVADO: Disconnecting WireGuard"
  cleanup_wireguard_state
}

# Check if VPN connection is active
check_connection() {
  log "INFO: PRIVADO: Checking connection"

  local N=10
  while [[ ${N} -gt 0 ]]; do
    # Check if wg0 interface exists
    if ip link show wg0 &>/dev/null; then
      # Check if we have a recent handshake
      local handshake
      handshake=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}')

      if [[ -n "${handshake}" ]] && [[ "${handshake}" != "0" ]]; then
        local now
        now=$(date +%s)
        local age=$((now - handshake))

        if [[ ${age} -lt 180 ]]; then
          log "INFO: PRIVADO: Connected (handshake ${age}s ago)"
          return 0
        fi
      fi

      # Interface exists but no recent handshake - might still be connecting
      log "INFO: PRIVADO: Waiting for handshake..."
    fi

    sleep 3
    N=$((N - 1))
  done

  log "ERROR: PRIVADO: Cannot establish connection"
  exit 1
}

# Get current public IP through VPN
get_public_ip() {
  curl -sS --max-time 10 https://api.ipify.org 2>/dev/null || \
  curl -sS --max-time 10 https://ifconfig.me 2>/dev/null || \
  echo "unknown"
}
