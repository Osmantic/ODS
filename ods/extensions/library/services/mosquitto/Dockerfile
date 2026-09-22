# syntax=docker/dockerfile:1
FROM eclipse-mosquitto:2.0.22@sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21
USER root
RUN mkdir -p /mosquitto/data && chown -R mosquitto:mosquitto /mosquitto/data
COPY mosquitto.conf acl /mosquitto/config/
COPY --chmod=0555 start.sh /ods-start.sh
USER mosquitto
ENTRYPOINT ["/bin/sh", "/ods-start.sh"]
