FROM listmonk/listmonk:latest@sha256:f535d59e14991337a9f2d570273685378ae86b0d7698c3e00da444e3bc205286
USER root
RUN mkdir -p /listmonk/uploads && chown -R 1000:1000 /listmonk/uploads
COPY --chmod=0555 start.sh /listmonk/ods-start.sh
USER 1000:1000
ENTRYPOINT ["/listmonk/ods-start.sh"]
