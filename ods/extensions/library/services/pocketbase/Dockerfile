FROM --platform=$BUILDPLATFORM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS fetch
RUN apk add --no-cache ca-certificates unzip
ARG TARGETARCH
COPY fetch.sh /fetch.sh
RUN sh /fetch.sh "$TARGETARCH" && mkdir -p /workspace/pb_data /workspace/pb_hooks /workspace/pb_migrations /workspace/pb_public
FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0
COPY --from=fetch /out/pocketbase /pocketbase
COPY --from=fetch /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=fetch --chown=1000:1000 /workspace /workspace
USER 1000:1000
