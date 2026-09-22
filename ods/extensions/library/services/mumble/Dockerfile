FROM mumblevoip/mumble-server:v1.5.915@sha256:b2d6b2d44b712232af90bdef37bfcbbf4e2f9819aee6bf8b7f46588b34973f6f
USER root
RUN mkdir -p /data && chown -R 10000:10000 /data && chmod 0700 /data
COPY --chmod=0555 start.sh /ods-start.sh
USER 10000:10000
ENTRYPOINT ["/ods-start.sh"]
CMD ["/usr/bin/mumble-server"]
