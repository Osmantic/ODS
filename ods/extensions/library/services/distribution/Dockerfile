FROM registry:3@sha256:fd374bae807c225661adfe2c0c1f9970a0b8fab1761fd7dfb91e0fd9a8748f9b
RUN apk add --no-cache apache2-utils curl && mkdir -p /var/lib/registry && chown 1000:1000 /var/lib/registry && chmod 0700 /var/lib/registry
COPY config.yml /etc/distribution/config.yml
COPY --chmod=0555 start.sh /ods-start.sh
COPY --chmod=0555 health.sh /ods-health.sh
USER 1000:1000
ENTRYPOINT ["/ods-start.sh"]
CMD ["/etc/distribution/config.yml"]
