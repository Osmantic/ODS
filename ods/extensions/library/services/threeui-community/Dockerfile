# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
WORKDIR /app
ADD --checksum=sha256:7ee319263e09816fb05e70b11a68e2956be610eb4cfcca30a306f44c0e910bd1 https://codeload.github.com/MengTo/threeui/tar.gz/68802d5428071ada5c20db8094b1649e6bb770ed /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
RUN npm ci --no-audit --no-fund && npm run build
RUN mkdir -p /packages && npm pack --ignore-scripts --pack-destination /packages && mv /packages/designcodeio-threeui-1.2.0.tgz /packages/threeui-community-1.2.0.tgz
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=build /packages /usr/share/nginx/html/packages
COPY --from=build /app/LICENSE /app/ASSET-LICENSES.md /app/FONT-LICENSES.md /app/THIRD_PARTY_NOTICES.md /usr/share/nginx/html/licenses/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
