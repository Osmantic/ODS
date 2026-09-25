FROM nats:2.15.0-alpine@sha256:ac8f88a6494bffc2c2a5289a0ca61cb28a9145c11ba5677cf24265d07f46d8d4
USER root
RUN mkdir -p /data && chown 1000:1000 /data && chmod 0700 /data
COPY --chmod=0555 start.sh /ods-start.sh
USER 1000:1000
ENTRYPOINT ["/ods-start.sh"]
