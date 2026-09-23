FROM node:22-trixie

ARG CODEX_VERSION=0.147.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends acl curl git jq openssh-client python3 ripgrep rsync sudo util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --ignore-scripts=false "@openai/codex@${CODEX_VERSION}"
