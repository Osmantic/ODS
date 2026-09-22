FROM ghcr.io/silverbulletmd/silverbullet:2.11.0@sha256:535efe0a5d97587593edd88dfd7b2523f7c3a11482aa5c75887cce64f741acfc
USER root
# The official image intentionally removes stock passwd/group entries. Supply a
# stable identity for nonroot file ownership, Chromium, and optional Git/SSH use.
RUN addgroup -g 1000 silverbullet && adduser -D -G silverbullet -u 1000 -h /home/silverbullet silverbullet \
    && mkdir -p /data /home/silverbullet \
    && chown -R 1000:1000 /data /home/silverbullet
USER 1000:1000
