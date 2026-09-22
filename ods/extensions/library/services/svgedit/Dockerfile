# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:5e73cbcc0a37c378223664ea76e74feadf92b35837a7d9a683f0e21f51096050 https://codeload.github.com/SVG-Edit/svgedit/tar.gz/c44f061d2f9a626d2771cc931298af5a45522d87 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --no-audit --no-fund && npm run build
RUN rm -rf /app/dist/editor/tests && rm -f /app/dist/editor/xdomain-index.html /app/dist/editor/iife-index.html
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/dist/editor /usr/share/nginx/html
COPY --from=build /app/LICENSE-MIT.txt /app/AUTHORS /app/licenseInfo.json /usr/share/nginx/html/licenses/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
