#!/bin/sh
set -eu
umask 077
mkdir -p /data/books
# Regenerate only the ODS-managed index, never the user's ZIM archives.
# A failed import must not replace the last complete library index.
library=/data/ods-library.next.xml
trap 'rm -f /data/ods-library.next.xml' EXIT HUP INT TERM
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?><library version="20110515"></library>' > "$library"
for archive in /data/books/*.zim; do
  [ -e "$archive" ] || continue
  [ -f "$archive" ] || continue
  /usr/local/bin/kiwix-manage "$library" add "$archive"
done
mv "$library" /data/ods-library.xml
exec /usr/local/bin/kiwix-serve --port=8080 --address=0.0.0.0 --threads=4 --ipConnectionLimit=12 --blockexternal --library /data/ods-library.xml
