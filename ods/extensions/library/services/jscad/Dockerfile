# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
RUN apk add --no-cache git
WORKDIR /app
ADD --checksum=sha256:5232a1e5c40d97dd1b6036a036bcd3b1753af6f074d475ae821e058558076df6 https://codeload.github.com/jscad/OpenJSCAD.org/tar.gz/f245ea3a5072024b789f276c6fb9ce6c3eb3fd0d /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app \
    && npm ci --no-audit --no-fund \
    && npm run build --workspace=@jscad/web \
    && test -s packages/web/examples/examples.json \
    && cp packages/web/demo.html packages/web/index.html
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/packages/web/index.html /usr/share/nginx/html/index.html
COPY --from=build /app/packages/web/dist /usr/share/nginx/html/dist
COPY --from=build /app/packages/web/css /usr/share/nginx/html/css
COPY --from=build /app/packages/web/fonts /usr/share/nginx/html/fonts
COPY --from=build /app/packages/web/imgs /usr/share/nginx/html/imgs
COPY --from=build /app/packages/web/examples /usr/share/nginx/html/examples
COPY --from=build /app/LICENSE /usr/share/nginx/html/LICENSE
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
