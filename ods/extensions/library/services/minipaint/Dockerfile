# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
RUN apk add --no-cache git
WORKDIR /app
ADD --checksum=sha256:21f1cadb54397f21927194d762fcef32282b0b2ec501a2cfaf65af9008c34fb4 https://codeload.github.com/viliusle/miniPaint/tar.gz/a79733eb803fc97084ef0ee4faa96b031e69e1c0 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
RUN npm ci --no-audit --no-fund && npm run build
RUN mkdir /site && cp -r dist images src /site/ && cp index.html service-worker.js MIT-LICENSE.txt /site/
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /site /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
