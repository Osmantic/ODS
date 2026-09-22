FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
COPY health.go .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -trimpath -ldflags="-s -w" -o /health health.go \
    && mkdir -p /data && touch /data/.keep

FROM jaegertracing/jaeger:2.21.0@sha256:3d0ac795ff98aa04d1be04311d2dac6c25b4bfc8322dc02e53bc5b170c5018c3
COPY --from=build /health /opt/ods-health
COPY --from=build --chown=10001:10001 /data /data
COPY config.yaml /etc/ods-jaeger.yaml
USER 10001:10001
CMD ["--config", "/etc/ods-jaeger.yaml"]
