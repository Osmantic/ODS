FROM traccar/traccar:6.15.3-alpine@sha256:35346b4af46d57adaeff69a548a7d2cb189a2487fe9c24c99044ee7aa104b5df
USER root
RUN mkdir -p /opt/traccar/data /opt/traccar/media && chown -R 1000:1000 /opt/traccar/data /opt/traccar/media
COPY traccar.xml /opt/traccar/conf/traccar.xml
COPY --chmod=0555 start.sh /opt/traccar/ods-start.sh
USER 1000:1000
ENTRYPOINT ["/opt/traccar/ods-start.sh"]
