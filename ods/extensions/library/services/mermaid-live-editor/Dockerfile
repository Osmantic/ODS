FROM --platform=$BUILDPLATFORM node:24.16.0-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
RUN apk add --no-cache build-base git python3 && corepack enable pnpm
WORKDIR /app
ADD --checksum=sha256:1b9e11583b948a25fb6264b3b13493f7a86d7917887e0c2c6430be23b4c848fd https://codeload.github.com/mermaid-js/mermaid-live-editor/tar.gz/8754416d847d2947c0492bc045cddbab717a51ea /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /app
ENV HUSKY=0
ENV MERMAID_RENDERER_URL="" MERMAID_KROKI_RENDERER_URL="" MERMAID_ANALYTICS_URL="" MERMAID_DOMAIN="" MERMAID_IS_ENABLED_MERMAID_CHART_LINKS=""
RUN pnpm install --frozen-lockfile && pnpm build
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY --from=build /app/docs /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
