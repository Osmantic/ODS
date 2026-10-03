FROM ghcr.io/garethgeorge/backrest:v1.14.1@sha256:b852979754281026230cc69fb11428e6d57c9a97784ab4a444ffc7934c53a215
RUN mkdir -p /data /config /cache /sources /repos /restore \
    && chown -R 1000:1000 /data /config /cache /sources /repos /restore
USER 1000:1000
ENTRYPOINT ["/sbin/tini", "--", "/backrest"]
CMD []
