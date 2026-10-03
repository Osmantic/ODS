FROM restic/rest-server:latest@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5 AS upstream
FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0
RUN apk add --no-cache apache2-utils curl ca-certificates \
 && addgroup -g 1000 ods && adduser -D -u 1000 -G ods ods \
 && mkdir /data && chown 1000:1000 /data && chmod 0700 /data
COPY --from=upstream /usr/bin/rest-server /usr/local/bin/rest-server
COPY LICENSE.upstream /usr/share/licenses/rest-server/LICENSE
COPY start.sh /ods-start.sh
COPY health.sh /ods-health.sh
USER 1000:1000
ENTRYPOINT ["sh", "/ods-start.sh"]
