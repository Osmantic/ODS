FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS source
RUN wget -O /tmp/source.tgz https://codeload.github.com/openspeedtest/Speed-Test/tar.gz/f4263546f50694a154fdd27a03000390949068df \
    && echo "f8d239bc4183c214c0747ec1a1c418fd33773683f82e7ee8327cf734ea6a4987  /tmp/source.tgz" | sha256sum -c - \
    && mkdir /source && tar -xzf /tmp/source.tgz -C /source --strip-components=1
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY --from=source /source/index.html /source/downloading /source/upload /source/License.md /usr/share/nginx/html/
COPY --from=source /source/assets/ /usr/share/nginx/html/assets/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
