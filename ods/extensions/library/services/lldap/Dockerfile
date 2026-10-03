FROM lldap/lldap:v0.6.3@sha256:2a8454b668c1aba7157e832eab0e242e1e7eb5fb7591d7e7774ba05286511ca8
USER root
RUN mkdir -p /data && chown 1000:1000 /data
COPY ods-start.sh /ods-start.sh
COPY lldap_config.toml /app/ods-default.toml
USER 1000:1000
