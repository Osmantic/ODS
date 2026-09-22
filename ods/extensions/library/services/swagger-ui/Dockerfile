FROM docker.swagger.io/swaggerapi/swagger-ui:v5.33.0@sha256:f9b8432be04e320406157e26c3ff52a7e9a4bea7eabe5477e9636791737eb119
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html ods-initializer.js /usr/share/nginx/html/
USER 101:101
