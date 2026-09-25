FROM ghcr.io/requarks/wiki:2.5.314@sha256:68f0d1848261ae76492ba358e30a96a76fed5d97a3fff381656082bf90f70d7e
COPY config.yml /wiki/config.yml
COPY start.cjs /opt/ods/start.cjs
CMD ["node", "--require", "/opt/ods/start.cjs", "--no-deprecation", "server"]
