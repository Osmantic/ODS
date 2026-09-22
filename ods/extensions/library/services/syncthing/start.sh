#!/bin/sh
set -eu
umask 077
if [ ! -f /var/syncthing/.ods-initialized ]; then
  if [ -f /var/syncthing/config/config.xml ] && [ ! -f /var/syncthing/.ods-bootstrap-pending ]; then
    echo 'Existing Syncthing configuration requires explicit migration; refusing to replace folders or GUI credentials' >&2
    exit 1
  fi
  case "${SYNCTHING_GUI_PASSWORD:-}" in
    ''|*[!a-fA-F0-9]*) echo 'SYNCTHING_GUI_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1 ;;
  esac
  [ "${#SYNCTHING_GUI_PASSWORD}" -eq 64 ] || { echo 'SYNCTHING_GUI_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1; }
  touch /var/syncthing/.ods-bootstrap-pending
  printf '%s\n' "$SYNCTHING_GUI_PASSWORD" | syncthing generate --gui-user ods --gui-password - --no-port-probing
  python3 /opt/configure.py
  rm /var/syncthing/.ods-bootstrap-pending
fi
unset SYNCTHING_GUI_PASSWORD
exec syncthing serve --no-browser --no-restart
