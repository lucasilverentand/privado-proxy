#!/bin/bash

# Microsocks SOCKS5 proxy setup

setup_dante() {
  log "INFO: PROXY: Setting up microsocks SOCKS5 proxy"
  # microsocks doesn't need config file setup - it uses command line args
  log "INFO: PROXY: Will bind to 0.0.0.0:${SOCK_PORT}"
}

start_dante() {
  log "INFO: PROXY: Starting microsocks"
  supervisorctl start microsocks
}
