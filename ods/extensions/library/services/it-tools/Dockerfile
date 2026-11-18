FROM node:24.16-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a AS build
RUN apk add --no-cache curl patch python3 && npm install -g pnpm@8.15.3
WORKDIR /src
RUN curl -fsSL https://codeload.github.com/CorentinTh/it-tools/tar.gz/7ca59331784bfe564cfbca2a5b1971ea7ae82735 -o /tmp/upstream.tar.gz \
 && echo '1829b78fdea40d9a4a9aaf0c6aa7131e33898351e713356af07941959d3407d4  /tmp/upstream.tar.gz' | sha256sum -c - \
 && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /src
COPY csv-rfc4180.patch test-csv-roundtrip.py /tmp/
RUN echo 'f2f8903ce5f71765a4d7cc9c84900af8236ce75c6a93560852fb484df18d2f9a  src/tools/json-to-csv/json-to-csv.service.ts' | sha256sum -c - \
 && patch --fuzz=0 -p1 < /tmp/csv-rfc4180.patch \
 && python3 /tmp/test-csv-roundtrip.py src/tools/json-to-csv/json-to-csv.service.ts
ENV CI=true
RUN pnpm install --frozen-lockfile && pnpm build

FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY --from=build /src/dist /usr/share/nginx/html
COPY --from=build /src/LICENSE /usr/share/nginx/html/ITTOOLS-LICENSE.txt
COPY --from=build /tmp/upstream.tar.gz /usr/share/nginx/html/source/upstream.tar.gz
COPY --chmod=0644 csv-rfc4180.patch /usr/share/nginx/html/source/csv-rfc4180.patch
COPY --chmod=0644 ITTOOLS-SOURCE.txt /usr/share/nginx/html/ITTOOLS-SOURCE.txt
COPY --chmod=0644 nginx.conf /etc/nginx/nginx.conf
USER 101:101
