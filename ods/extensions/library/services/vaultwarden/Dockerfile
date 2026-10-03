FROM vaultwarden/server:1.37.3-alpine@sha256:9a905c5cf5df61bb827da3805e160170741c0b53b0c63bb07e1afd3d2e21b1c8
USER root
RUN mkdir -p /data && chown 1000:1000 /data && chmod 0700 /data
COPY --chmod=0555 start.sh /ods-start.sh
USER 1000:1000
ENTRYPOINT ["/ods-start.sh"]
CMD ["/start.sh"]
