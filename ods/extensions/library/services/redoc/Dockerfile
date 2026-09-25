FROM redocly/redoc:v2.5.3@sha256:2e26bb660574c9b85eacd8e67b4e7a78641f5b63252859450688aeec6d918807 AS assets
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY --from=assets /usr/share/nginx/html/redoc.standalone.js /usr/share/nginx/html/redoc.standalone.js
COPY index.html ods-reader.js /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
