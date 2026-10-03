FROM --platform=$BUILDPLATFORM golang:1.26-alpine@sha256:51a7c389a5ddaf82f527191a1e9bff9928655130a44e4975dd1d7e0acf59f1ae AS build
WORKDIR /src
ADD --checksum=sha256:1815e5995dea0aa0b598de7da61dff34a12263e5c9c1fa858ee31ce3eb5298b0 https://github.com/go-shiori/shiori/archive/da56f82faac5bf350fc4175e5ff50726cb10d956.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz -C /src --strip-components=1
COPY patch.go /tmp/patch.go
RUN go run /tmp/patch.go
COPY ods_initial_password.go internal/cmd/ods_initial_password.go
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} go build -tags netgo,osusergo -trimpath -ldflags="-s -w -X github.com/go-shiori/shiori/internal/model.BuildVersion=1.8.0-ods.1 -X github.com/go-shiori/shiori/internal/model.BuildCommit=da56f82faac5bf350fc4175e5ff50726cb10d956" -o /shiori .
FROM ghcr.io/go-shiori/shiori:alpine-v1.8.0@sha256:79ca8588c0abc5cc1ccad11ce5ab7f9bab393966b699d60be9c82aa6b356fd3a
COPY --from=build /shiori /usr/bin/shiori
RUN mkdir -p /shiori && chown 1000:1000 /shiori
USER 1000:1000
