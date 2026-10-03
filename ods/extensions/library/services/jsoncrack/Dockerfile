FROM node:24.16-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS source
RUN apk add --no-cache curl libc6-compat && npm install -g pnpm@10.20.0
WORKDIR /src
RUN curl -fsSL https://codeload.github.com/AykutSarac/jsoncrack.com/tar.gz/a46d0d0adb098e12e577a083e87ead7f61f90098 -o /tmp/source.tgz \
 && echo 'f3eaf13430771e38a9436b19e7457b522bcddadca93b8c82ceac7ada09037126  /tmp/source.tgz' | sha256sum -c - \
 && tar -xzf /tmp/source.tgz --strip-components=1 -C /src
RUN pnpm dlx turbo@2.8.20 prune www --docker

FROM source AS build
WORKDIR /build
COPY --from=source /src/out/json/ ./
COPY --from=source /src/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=source /src/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY --from=source /src/out/full/ ./
ENV NEXT_TELEMETRY_DISABLED=1 TURBO_TELEMETRY_DISABLED=1 NEXT_PUBLIC_NODE_LIMIT=10000 NEXT_PUBLIC_DISABLE_EXTERNAL_MODE=true
# ODS modification: serve the pinned Monaco assets locally, not from unpkg.
RUN node -e 'const fs=require("fs"); const p="apps/www/src/features/editor/TextEditor.tsx"; const s=fs.readFileSync(p,"utf8"); const old="https://unpkg.com/monaco-editor@0.55.1/min/vs"; if(!s.includes(old))throw Error("Monaco source changed");fs.writeFileSync(p,s.replace(old,"/monaco/vs"));'
RUN pnpm turbo run build --filter=www --env-mode=loose
RUN curl -fsSL https://registry.npmjs.org/monaco-editor/-/monaco-editor-0.55.1.tgz -o /tmp/monaco.tgz \
 && echo 'eec3721fb6b1dc5a0bd1a73e38a5eb5d0c3791af684f7d2571efb90ad8634871  /tmp/monaco.tgz' | sha256sum -c - \
 && mkdir -p /tmp/monaco apps/www/out/monaco \
 && tar -xzf /tmp/monaco.tgz -C /tmp/monaco \
 && cp -R /tmp/monaco/package/min/vs apps/www/out/monaco/vs \
 && cp /tmp/monaco/package/LICENSE apps/www/out/monaco/LICENSE

FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY --from=build /build/apps/www/out /usr/share/nginx/html
COPY --from=source /src/LICENSE.md /usr/share/nginx/html/JSONCRACK-LICENSE.txt
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
