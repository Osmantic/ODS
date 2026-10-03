FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
COPY health.go .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -trimpath -ldflags="-s -w" -o /ods-health health.go
FROM ghcr.io/mccutchen/go-httpbin:2.25.0@sha256:20739736d4eb8dc1b998dff701f437b8bd62dcc46492bd0d861e89890ca36500
COPY --from=build /ods-health /bin/ods-health
USER 65532:65532
