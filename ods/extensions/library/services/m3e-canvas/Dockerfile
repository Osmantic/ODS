# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:e2cf5777cd336723b399766aa239fa40a2753b4c7d3a9d1b09c2556bb70b8c80 https://codeload.github.com/lnkiai/m3e-canvas/tar.gz/dea74159d69b09fb6fc2e44ebebf5e62c043e1ce /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_BASE_PATH=""
RUN npm ci --no-audit --no-fund && npm run build
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/out /usr/share/nginx/html
COPY --from=build /app/LICENSE /app/NOTICE /usr/share/nginx/html/licenses/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
