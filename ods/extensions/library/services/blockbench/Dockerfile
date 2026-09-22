# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:f520c672a2295b342b1f29dc203914240fb92b2abec6936d7ac549fdbf782751 https://codeload.github.com/JannisX11/blockbench/tar.gz/2569d0245d600030760cf0b8429b1b0017b871f1 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
# Desktop-only native addons and Electron downloads are not needed for the web target.
RUN npm ci --ignore-scripts --no-audit --no-fund && npm rebuild esbuild && npm run build-web
RUN mkdir -p /site/source && cp -r assets content css font icons keymaps lang lib themes dist /site/ && cp index.html manifest.webmanifest favicon.png icon.png icon_full.png icon_maskable.png LICENSE.MD /site/ && cp /tmp/source.tar.gz /site/source/blockbench-5.2.0.tar.gz
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /site /usr/share/nginx/html
COPY Dockerfile nginx.conf compose.yaml manifest.yaml upstream.json README.md /usr/share/nginx/html/source/ods-recipe/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
