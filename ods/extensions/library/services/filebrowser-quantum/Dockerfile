FROM ghcr.io/gtsteffaniak/filebrowser:1.5.6-stable@sha256:7c5d7ac8ffda31294d278063cf9d2e04303b39e6dce1f4c691342240ca7703b8
USER root
RUN mkdir -p /srv /home/filebrowser/data/tmp && chown -R 1000:1000 /srv /home/filebrowser/data
COPY --chown=1000:1000 config.yaml /home/filebrowser/data/config.yaml
USER 1000:1000
