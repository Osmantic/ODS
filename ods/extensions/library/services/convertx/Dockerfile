FROM ghcr.io/c4illin/convertx:v0.18.0@sha256:b515b04bfd25298a5cdc775b2fcd48b9399bab658ce13e2598b65df1b16098c8
USER root
# Upstream installs MarkItDown with pipx under /root, including absolute
# interpreter paths. Permit traversal of those image assets without moving
# the virtualenv or granting writes to the unprivileged runtime user.
RUN chmod o+x /root && chmod -R a+rX /root/.local \
    && mkdir -p /app/data/home && chown -R 1000:1000 /app/data
ENV HOME=/app/data/home
USER 1000:1000
