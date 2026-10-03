FROM penpotapp/frontend:2.17.2@sha256:94fa2864d8fc0cd62245af95c03cca89306a7fd23c206a98a3e9dc9a376ea27e
# The community stack does not ship the separate Nitrate admin console.
# Remove its static DNS dependency; preserve the ordinary Penpot application.
USER root
RUN sed -i '/        location \/admin-console {/,/        }/c\        location /admin-console { return 404; }' /tmp/nginx.conf.template
USER penpot:penpot
