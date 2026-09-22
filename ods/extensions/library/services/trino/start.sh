#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /tmp/trino/catalog /tmp/trino-data
cp /etc/ods-trino/config/*.properties /etc/ods-trino/config/jvm.config /etc/ods-trino/config/rules.json /tmp/trino/
cp /etc/ods-trino/catalog/*.properties /tmp/trino/catalog/
printf 'ods:%s\n' "${TRINO_PASSWORD_HASH:?}" > /tmp/trino/password.db
if [[ ! -f /var/lib/ods-trino/tls/server.p12 ]]; then
    keytool -genkeypair -alias ods-trino -keyalg RSA -keysize 3072 -validity 365 \
        -dname 'CN=localhost, OU=Local ODS Trino' \
        -ext 'SAN=dns:localhost,dns:trino,ip:127.0.0.1' \
        -keystore /var/lib/ods-trino/tls/server.p12 -storetype PKCS12 \
        -storepass:env TRINO_TLS_PASSWORD -noprompt
fi
keytool -exportcert -alias ods-trino -rfc \
    -keystore /var/lib/ods-trino/tls/server.p12 -storepass:env TRINO_TLS_PASSWORD \
    -file /var/lib/ods-trino/tls/server.crt
exec /usr/lib/trino/bin/launcher run --etc-dir /tmp/trino
