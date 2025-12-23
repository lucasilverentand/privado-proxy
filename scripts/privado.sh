#!/bin/bash

# Privado VPN API endpoints
CLIENT_API_URL="https://client-api.privado.io/v1"
API_KEY="9f994c466340e8f2ed60a99396fecb6a"
USER_AGENT="App: 3.0.0 (576942783), macOS: Version 12.4 (Build 21F79)"

# Data directory for storing session data
DATA_DIR="/run/privado"
TOKEN_FILE="${DATA_DIR}/token.json"
SERVERS_FILE="${DATA_DIR}/servers.json"
WG_CONFIG="/etc/wireguard/wg0.conf"

# Initialize data directory
init_privado() {
  mkdir -p ${DATA_DIR}
  mkdir -p /etc/wireguard
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
  local query_lower=$(echo "${query}" | tr '[:upper:]' '[:lower:]')

  # Try to find by exact server name first (excluding maintenance servers)
  server_name=$(jq -r --arg query "${query}" '.[] | select(.maintenance == false) | select(.name == $query) | .name' "${SERVERS_FILE}" | head -1)

  # If not found, check if query is in country-city format (e.g., "nl-ams", "netherlands-amsterdam")
  if [[ -z "${server_name}" ]] && [[ "${query}" == *-* ]]; then
    local country_part=$(echo "${query}" | cut -d'-' -f1 | tr '[:upper:]' '[:lower:]')
    local city_part=$(echo "${query}" | cut -d'-' -f2- | tr '[:upper:]' '[:lower:]')
    
    # Try to find server matching both country (partial) and city (partial) in respective fields
    server_name=$(jq -r --arg country "${country_part}" --arg city "${city_part}" '.[] | select(.maintenance == false) | select((.country | ascii_downcase | contains($country)) and (.city | ascii_downcase | contains($city))) | .name' "${SERVERS_FILE}" | head -1)
    
    # If still not found, try flexible matching where city_part matches server name start 
    # and country_part matches country field (useful for codes like "nl-ams" where server is "ams-001...")
    if [[ -z "${server_name}" ]]; then
      server_name=$(jq -r --arg country "${country_part}" --arg city "${city_part}" '.[] | select(.maintenance == false) | select((.country | ascii_downcase | contains($country)) and (.name | ascii_downcase | startswith($city))) | .name' "${SERVERS_FILE}" | head -1)
    fi
    
    # If still not found with country constraint, try just city_part at start of server name
    # This handles cases like "nl-ams" where "nl" can't be matched to "Netherlands" but "ams" identifies the server
    if [[ -z "${server_name}" ]]; then
      server_name=$(jq -r --arg city "${city_part}" '.[] | select(.maintenance == false) | select(.name | ascii_downcase | startswith($city)) | .name' "${SERVERS_FILE}" | head -1)
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

# Get WireGuard configuration from server
get_wireguard_config() {
  log "INFO: PRIVADO: Getting WireGuard configuration"

  # Find the server hostname
  local server_hostname
  server_hostname=$(find_server "${PRIVADO_SERVER}")
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

  # Use configured DNS
  WG_DNS="${DNS}"

  if [[ -z "${WG_PRIVATE_KEY}" ]] || [[ -z "${WG_ADDRESS}" ]] || [[ -z "${WG_SERVER_PUBLIC_KEY}" ]] || [[ -z "${WG_SERVER_IP}" ]]; then
    log "ERROR: PRIVADO: Incomplete WireGuard configuration received"
    log "DEBUG: Response: ${response}"
    exit 1
  fi

  log "INFO: PRIVADO: WireGuard configuration obtained"
  log "INFO: PRIVADO: Interface IP: ${WG_ADDRESS}"

  # Save server hostname for later use
  PRIVADO_SERVER_HOSTNAME="${server_hostname}"
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
  local default_gw default_if
  default_gw=$(ip route | grep "^default" | awk '{print $3}' | head -1)
  default_if=$(ip route | grep "^default" | awk '{print $5}' | head -1)
  log "INFO: PRIVADO: Original gateway: ${default_gw} via ${default_if}"

  # Create WireGuard interface
  ip link add wg0 type wireguard
  wg setconf wg0 "${WG_CONFIG}"
  ip addr add "${WG_ADDRESS}/32" dev wg0
  ip link set wg0 up

  # Add route to VPN server via original gateway (so we can reach it)
  ip route add "${WG_SERVER_IP}/32" via "${default_gw}" dev "${default_if}" 2>/dev/null || true

  # Replace default route with WireGuard tunnel
  ip route del default 2>/dev/null || true
  ip route add default dev wg0

  if ! ip link show wg0 up &>/dev/null; then
    log "ERROR: PRIVADO: Failed to bring up WireGuard interface"
    exit 1
  fi

  log "INFO: PRIVADO: WireGuard interface is up"
}

# Disconnect from VPN
disconnect_privado() {
  log "INFO: PRIVADO: Disconnecting WireGuard"
  wg-quick down wg0 2>/dev/null || true
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
