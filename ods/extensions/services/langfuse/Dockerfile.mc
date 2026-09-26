# ods-langfuse-mc:RELEASE.2025-08-13T08-35-41Z
# Source-built MinIO client (mc), upstream release tag pinned and commit-verified.
# Upstream minio/mc is archived; official images and checksum URLs are gone,
# so we build the SAME source version.
# Verified: release tag RELEASE.2025-08-13T08-35-41Z peels to
#   commit 7394ce0dd2a80935aded936b09fa12cbb3cb8096
#   (annotated tag object d6541ea280b73a834b64d4097e21f2be77676104)

FROM golang:1.27.1-bookworm@sha256:69a7b9788769bec032d238959b61854e9ae87f57be9029ec04e9885fabf99195 AS build

ARG MC_RELEASE=RELEASE.2025-08-13T08-35-41Z
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096

# Fixed compiler, no floating toolchain downloads.
ENV GOTOOLCHAIN=local \
    CGO_ENABLED=0 \
    GOPATH=/go \
    GOMODCACHE=/go/pkg/mod

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Fetch the exact release tag over HTTPS, then hard-verify the peeled commit.
RUN git clone --depth 1 --branch "${MC_RELEASE}" \
      https://github.com/minio/mc.git /src/mc
RUN cd /src/mc \
 && actual="$(git rev-parse HEAD)" \
 && echo "peeled commit: ${actual}" \
 && test "${actual}" = "${MC_COMMIT}"

WORKDIR /src/mc

RUN mkdir -p /out/licenses \
 && cp LICENSE /out/licenses/LICENSE \
 && for notice in NOTICE CREDITS; do if [ ! -f "$notice" ]; then continue; fi; cp "$notice" /out/licenses/; done

# Bounded parallelism for 16GB host; go.sum/sumdb verification stays enabled.
RUN go build -p 2 -trimpath \
      -ldflags "$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${MC_RELEASE#RELEASE.}")" \
      -o /out/mc .

# Sanity: binary runs and reports the pinned release identity.
RUN /out/mc --version | grep -q "${MC_RELEASE}"

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

ARG MC_RELEASE=RELEASE.2025-08-13T08-35-41Z
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096

LABEL org.opencontainers.image.title="ods-langfuse-mc" \
      org.opencontainers.image.version="${MC_RELEASE}" \
      org.opencontainers.image.revision="${MC_COMMIT}" \
      org.opencontainers.image.source="https://github.com/minio/mc" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# ca-certificates required for https endpoints; /bin/sh kept for the existing
# Compose entrypoint override (bucket-init script). No credentials baked in.
RUN apk add --no-cache ca-certificates \
 && mkdir -p /licenses

COPY --from=build /out/mc /usr/bin/mc
COPY --from=build /out/licenses/ /licenses/

RUN chmod +x /usr/bin/mc

ENTRYPOINT ["mc"]
CMD ["--help"]
