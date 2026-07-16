#!/bin/bash

TZ=${TZ:-'UTC'}
CONFIG_FILE=${CONFIG_FILE:-'/config/privado.env'}

if [[ -f ${CONFIG_FILE} ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${CONFIG_FILE}"
  set +a
fi

# Dante Settings
SOCK_PORT=${SOCK_PORT:-"1080"}

# Dashboard Settings
DASHBOARD_ENABLED=${DASHBOARD_ENABLED:-"false"}
DASHBOARD_PORT=${DASHBOARD_PORT:-"8080"}

# Privado Settings
PRIVADO_USERNAME=${PRIVADO_USERNAME:-''}
PRIVADO_PASSWORD=${PRIVADO_PASSWORD:-''}
PRIVADO_SERVER=${PRIVADO_SERVER:-''}

# Secret files (Docker secrets support)
PRIVADO_USERNAME_FILE=${PRIVADO_USERNAME_FILE:-'/run/secrets/privado_username'}
PRIVADO_PASSWORD_FILE=${PRIVADO_PASSWORD_FILE:-'/run/secrets/privado_password'}

# If no username is provided, check for a secret file
if [[ -z ${PRIVADO_USERNAME} ]] && [[ -f ${PRIVADO_USERNAME_FILE} ]]; then
  PRIVADO_USERNAME=$(cat "${PRIVADO_USERNAME_FILE}")
fi

# If no password is provided, check for a secret file
if [[ -z ${PRIVADO_PASSWORD} ]] && [[ -f ${PRIVADO_PASSWORD_FILE} ]]; then
  PRIVADO_PASSWORD=$(cat "${PRIVADO_PASSWORD_FILE}")
fi

# Network Settings
DNS=${DNS:-'9.9.9.9,149.112.112.112'} # Default Quad9 resolvers
HEALTHCHECK_URL=${HEALTHCHECK_URL:-'https://api.ipify.org'}
HEALTHCHECK_TIMEOUT=${HEALTHCHECK_TIMEOUT:-'20'}

# Networks
DOCKER_NET=${DOCKER_NET:-''}
LOCAL_SUBNETS=${LOCAL_SUBNETS:-'192.168.0.0/16,172.16.0.0/12,10.0.0.0/8'}
LOCALNET=$(hostname -i | grep -Eom1 "(^[0-9]{1,3}\.[0-9]{1,3})")

# Autofill docker network if not provided
if [[ -z ${DOCKER_NET} ]]; then
  DOCKER_NET="$(hostname -i | grep -Eom1 "^[0-9]{1,3}\.[0-9]{1,3}").0.0/12"
fi

print_settings() {
  log "INFO: Settings:"
  log "INFO: TZ: ${TZ}"
  log "INFO: SOCK_PORT: ${SOCK_PORT}"
  log "INFO: DASHBOARD_ENABLED: ${DASHBOARD_ENABLED}"
  log "INFO: DASHBOARD_PORT: ${DASHBOARD_PORT}"
  log "INFO: DNS: ${DNS}"
  log "INFO: HEALTHCHECK_URL: ${HEALTHCHECK_URL}"
  log "INFO: PRIVADO_SERVER: ${PRIVADO_SERVER}"
  log "INFO: DOCKER_NET: ${DOCKER_NET}"
  log "INFO: LOCAL_SUBNETS: ${LOCAL_SUBNETS}"
  log "INFO: LOCALNET: ${LOCALNET}"
  # Don't log credentials
  if [[ -n ${PRIVADO_USERNAME} ]]; then
    log "INFO: PRIVADO_USERNAME: [set]"
  else
    log "INFO: PRIVADO_USERNAME: [not set]"
  fi
  if [[ -n ${PRIVADO_PASSWORD} ]]; then
    log "INFO: PRIVADO_PASSWORD: [set]"
  else
    log "INFO: PRIVADO_PASSWORD: [not set]"
  fi
}
