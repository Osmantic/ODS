FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
COPY bridge.go .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -trimpath -ldflags="-s -w" -o /bridge bridge.go

FROM scratch
COPY --from=build /bridge /bridge
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/bridge"]
