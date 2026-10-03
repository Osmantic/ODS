FROM gristlabs/grist-oss:1.7.19@sha256:3e43d3afc9ca86a9df0d65c247e51556ce5463dde91b278bcf98ae3edc1e1701
USER root
RUN mkdir -p /persist/docs /persist/home && chown -R grist:grist /persist
ENV HOME=/persist/home
USER grist
