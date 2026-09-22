# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:0810daa530a2b7020e98c2f02ed58cfa32c8151eb048ee7b79ad59774b79f0b7 https://codeload.github.com/mbloch/mapshaper/tar.gz/629049657b54242552d3488b39e75b3a78cd68d2 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --no-audit --no-fund && npm run build && npm run docs
COPY basemap.js /app/www/basemap.js
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/www /usr/share/nginx/html
COPY --from=build /app/LICENSE /usr/share/nginx/html/LICENSE
COPY --from=build /tmp/source.tar.gz /usr/share/nginx/html/source/mapshaper-0.7.62.tar.gz
COPY Dockerfile basemap.js nginx.conf compose.yaml manifest.yaml upstream.json README.md /usr/share/nginx/html/source/ods-recipe/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
