FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
COPY launcher.go .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -trimpath -o /ods-launcher launcher.go && mkdir /data
FROM twinproduction/gatus:v5.36.0@sha256:c5f210d095fa78e6efaa20ffeb14803f2ba4f10615e16a6d12087697149617f0
COPY --from=build /ods-launcher /ods-launcher
COPY --from=build --chown=65532:65532 /data /data
USER 65532:65532
