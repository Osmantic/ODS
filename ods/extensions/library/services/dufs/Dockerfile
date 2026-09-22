FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
COPY launcher.go .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -trimpath -o /ods-launcher launcher.go && mkdir /data
FROM sigoden/dufs:v0.46.0@sha256:8dd3b750015734703e64b55641fb7d2254c112e0ff3967074766177dc15dc982
COPY --from=build /ods-launcher /ods-launcher
COPY --from=build --chown=65532:65532 /data /data
USER 65532:65532
