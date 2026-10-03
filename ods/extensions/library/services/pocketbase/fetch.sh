#!/bin/sh
set -eu
case "$1" in
  amd64) checksum=9042ec818570e79c3628dadcd0a756c1496d9e1173918ec409d133c02f82e5fa ;;
  arm64) checksum=86095bf8ed9345954f0d2bf0a5fb9b57584ae60b77ebf3b6cd23a8003a3fd418 ;;
  *) echo 'This PocketBase recipe supports amd64 and arm64.' >&2; exit 1 ;;
esac
wget -q -O /tmp/pocketbase.zip "https://github.com/pocketbase/pocketbase/releases/download/v0.40.4/pocketbase_0.40.4_linux_$1.zip"
printf '%s  /tmp/pocketbase.zip\n' "$checksum" | sha256sum -c -
mkdir -p /out
unzip -q /tmp/pocketbase.zip -d /out
chmod 755 /out/pocketbase
