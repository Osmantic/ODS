FROM dxflrs/garage:v2.3.0@sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690 AS upstream
FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0
COPY --from=upstream /garage /usr/local/bin/garage
RUN mkdir -p /var/lib/garage/meta /var/lib/garage/data && chown -R 1000:1000 /var/lib/garage
COPY garage.toml /etc/garage.toml
COPY --chmod=0555 start.sh /usr/local/bin/ods-garage-start
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/ods-garage-start"]
