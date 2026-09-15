FROM alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS release
ARG TARGETARCH
WORKDIR /release
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) checksum=9042ec818570e79c3628dadcd0a756c1496d9e1173918ec409d133c02f82e5fa ;; \
      arm64) checksum=86095bf8ed9345954f0d2bf0a5fb9b57584ae60b77ebf3b6cd23a8003a3fd418 ;; \
      *) printf 'Unsupported PocketBase architecture: %s\n' "$TARGETARCH" >&2; exit 1 ;; \
    esac; \
    wget -O release.zip "https://github.com/pocketbase/pocketbase/releases/download/v0.40.4/pocketbase_0.40.4_linux_${TARGETARCH}.zip"; \
    printf '%s  release.zip\n' "$checksum" | sha256sum -c -; \
    unzip release.zip pocketbase LICENSE.md

FROM alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
COPY --from=release /release/pocketbase /usr/local/bin/pocketbase
COPY --from=release /release/LICENSE.md /opt/pocketbase/LICENSE.md
COPY --chmod=755 start-pocketbase.sh /usr/local/bin/start-pocketbase.sh
COPY pb_hooks/ /opt/pocketbase/pb_hooks/
USER 1000:1000
WORKDIR /pb
ENTRYPOINT ["/usr/local/bin/start-pocketbase.sh"]
