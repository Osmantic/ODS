ARG SOURCE_DATE_EPOCH=0
FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates=20250419~deb12u1 libssl3=3.0.20-1~deb12u2 openssl=3.0.20-1~deb12u2 xz-utils=5.4.1-1+deb12u1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/*

ADD --checksum=sha256:9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578 https://nodejs.org/dist/v22.23.1/node-v22.23.1-linux-x64.tar.xz /tmp/node-runtime.tar.xz
ADD --checksum=sha256:d28b4fd4bd9f07ea71083d0cc40c579595cebbd4c10bc8ca98a6d385432e7255 https://registry.npmjs.org/@openai/codex/-/codex-0.147.0.tgz /tmp/codex.tgz
ADD --checksum=sha256:c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a https://registry.npmjs.org/@openai/codex/-/codex-0.147.0-linux-x64.tgz /tmp/codex-linux-x64.tgz

RUN install -d -m 0755 /opt/node /opt/codex/node_modules/@openai/codex /opt/codex/node_modules/@openai/codex-linux-x64 /opt/pixel/deploy/work-codex-provider /opt/pixel/scripts/lib /opt/pixel/schemas \
    && tar -xJf /tmp/node-runtime.tar.xz --strip-components=1 -C /opt/node \
    && test "$(/opt/node/bin/node --version)" = "v22.23.1" \
    && tar -xzf /tmp/codex.tgz --strip-components=1 -C /opt/codex/node_modules/@openai/codex \
    && tar -xzf /tmp/codex-linux-x64.tgz --strip-components=1 -C /opt/codex/node_modules/@openai/codex-linux-x64 \
    && sed -i '1c #!/opt/node/bin/node' /opt/codex/node_modules/@openai/codex/bin/codex.js \
    && chmod 0555 /opt/codex/node_modules/@openai/codex/bin/codex.js \
    && ln -s /opt/codex/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && test "$(HOME=/tmp/codex-version-home CODEX_HOME=/tmp/codex-version-home /usr/local/bin/codex --version)" = "codex-cli 0.147.0" \
    && rm -rf /tmp/codex-version-home /root/.codex \
    && rm -f /tmp/node-runtime.tar.xz /tmp/codex.tgz /tmp/codex-linux-x64.tgz

COPY --chmod=0444 deploy/work-codex-provider/container-entrypoint.mjs /opt/pixel/deploy/work-codex-provider/container-entrypoint.mjs
COPY --chmod=0444 deploy/work-codex-provider/container-protocol.mjs /opt/pixel/deploy/work-codex-provider/container-protocol.mjs
COPY --chmod=0444 deploy/work-codex-provider/codex-cli-qualification.mjs /opt/pixel/deploy/work-codex-provider/codex-cli-qualification.mjs
COPY --chmod=0444 scripts/lib/work-contract.mjs /opt/pixel/scripts/lib/work-contract.mjs
COPY --chmod=0444 scripts/lib/json-schema.mjs /opt/pixel/scripts/lib/json-schema.mjs
COPY --chmod=0444 schemas/*.json /opt/pixel/schemas/

RUN echo 'd0fb28935106a51515a8690b4f54af95592d53ec9f85d5068788cb0149e71079  /opt/pixel/deploy/work-codex-provider/container-entrypoint.mjs' | sha256sum -c - \
    && echo 'cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40  /opt/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex' | sha256sum -c - \
    && find /opt/pixel /opt/codex -type d -exec chmod 0555 {} + \
    && find /opt/pixel -type f -exec chmod 0444 {} +

LABEL org.opencontainers.image.title="Pixel isolated Codex advisory runner" \
      org.opencontainers.image.version="4.3.27" \
      org.opencontainers.image.base.name="debian:bookworm-slim" \
      org.osmantic.pixel.node-runtime="22.23.1" \
      org.osmantic.pixel.codex-version="0.147.0" \
      org.osmantic.pixel.codex-package-integrity="sha512-EQLEXecAG2ptxI7UpBMo2TR/ga5596/c/OsYF/0LoUDh5JANZ7IoGqlzBEWbuEVQ76JePIbtTW/ihCkp1a7Z3w==" \
      org.osmantic.pixel.codex-linux-x64-integrity="sha512-0W9MBxPpWW0cSkNqrTDN2jR7rzzT7oNMhQY5446lT2Lw5cz5yhDTck4Va9rjkQEm+HlFzP/dmEMSZbXfJsINmw==" \
      org.osmantic.pixel.codex-binary-sha256="cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40" \
      org.osmantic.pixel.entrypoint-sha256="d0fb28935106a51515a8690b4f54af95592d53ec9f85d5068788cb0149e71079"

ENTRYPOINT ["/opt/node/bin/node", "/opt/pixel/deploy/work-codex-provider/container-entrypoint.mjs"]
