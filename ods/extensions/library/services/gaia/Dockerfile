FROM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732

ARG GAIA_AGENT_UI_VERSION=0.19.0

LABEL org.opencontainers.image.title="ODS AMD GAIA Agent UI"
LABEL org.opencontainers.image.description="Experimental ODS extension image for AMD GAIA Agent UI"
LABEL org.opencontainers.image.source="https://github.com/amd/gaia"

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 gaia \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash gaia

RUN npm install -g "@amd-gaia/agent-ui@${GAIA_AGENT_UI_VERSION}" \
    && npm cache clean --force

COPY docker-entrypoint.sh /usr/local/bin/ods-gaia-entrypoint
RUN chmod 0755 /usr/local/bin/ods-gaia-entrypoint

USER gaia
WORKDIR /home/gaia

ENV HOME=/home/gaia
ENV NODE_ENV=production

EXPOSE 4200

ENTRYPOINT ["ods-gaia-entrypoint"]
