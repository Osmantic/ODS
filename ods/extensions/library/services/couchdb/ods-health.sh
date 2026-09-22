#!/bin/bash
set -euo pipefail
# The upstream final image removes curl. Compose bounds the connection timeout;
# read also bounds a listener that connects but fails to return HTTP headers.
exec 3<>/dev/tcp/127.0.0.1/5984
printf 'GET /_up HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3
IFS= read -r -t 4 status <&3
[[ "$status" =~ ^HTTP/1\.[01]\ 200\  ]]
