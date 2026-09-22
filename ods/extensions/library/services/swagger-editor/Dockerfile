FROM docker.swagger.io/swaggerapi/swagger-editor:latest-unprivileged@sha256:b56bd6a78cd9ba59378353d47bb07b689c18879c677b65fa62710daeac5af80f
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
