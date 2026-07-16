#!/usr/bin/env bash

log() {
  printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${0##*/}" "$*" >&2
}

set_timezone() {
  [[ ${TZ} == $(cat /etc/timezone) ]] && return
  log "INFO: Setting timezone to ${TZ}"
  ln -fs "/usr/share/zoneinfo/${TZ}" /etc/localtime
  dpkg-reconfigure -fnoninteractive tzdata
}
