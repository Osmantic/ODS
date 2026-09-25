# syntax=docker/dockerfile:1
FROM syncthing/syncthing:2.1.5@sha256:397aa00b92b48d65540ea3ae3cbf271b87bdccbe07a0b7bd7d2debc3a7b29138
USER root
RUN apk add --no-cache python3 && mkdir -p /var/syncthing && chown -R 1000:1000 /var/syncthing
COPY --chmod=0555 start.sh configure.py /opt/
USER 1000:1000
ENTRYPOINT ["/bin/sh", "/opt/start.sh"]
