FROM ghcr.io/gethomepage/homepage:v2.4.0@sha256:643bd0be730d40f69d58028a55d1a896739333e8815786df42bc97f109ecbe61
USER root
RUN mkdir -p /app/config/logs && chown -R 1000:1000 /app/config
COPY --chown=1000:1000 config/ /app/config/
USER 1000:1000
