# syntax=docker/dockerfile:1
FROM valkey/valkey:9.1.2-alpine@sha256:a0dbf4c1d5708782907c10e2c72deff317518518b5288a58416981d9db95d30b
USER root
RUN mkdir -p /data && chown -R 1000:1000 /data
COPY valkey.conf /etc/ods-valkey.conf
COPY --chmod=0555 start.sh health.sh /opt/
USER 1000:1000
ENTRYPOINT ["tini", "--", "/bin/sh", "/opt/start.sh"]
