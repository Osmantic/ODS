# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:8a4caf8744066d08005ce32034ba654d8114aa6875ff3267b56a68a9021d1c08 https://codeload.github.com/GoogleChromeLabs/squoosh/tar.gz/e8d35e0fb66eb16eff6fe8fc773eabcbb7128de3 /tmp/source.tar.gz
COPY disable-analytics.cjs /opt/disable-analytics.cjs
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app \
    && node /opt/disable-analytics.cjs \
    && HUSKY=0 npm ci --no-audit --no-fund \
    && npm run build \
    && test -s build/index.html
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/build /usr/share/nginx/html
COPY --from=build /app/LICENSE /usr/share/nginx/html/LICENSE
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
