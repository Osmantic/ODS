FROM owncast/owncast:0.3.0@sha256:0f35b568b1098d2badbde2dbf3c73a902009c95eb687c647edea04d6da079a78
USER root
RUN mkdir -p /app/data && chown -R 101:101 /app/data
COPY --chmod=0555 start.sh /app/ods-start.sh
USER 101:101
ENTRYPOINT ["/app/ods-start.sh"]
