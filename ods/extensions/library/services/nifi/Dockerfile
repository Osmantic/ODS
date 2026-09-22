FROM apache/nifi:latest@sha256:6f7f2840dbf178c6dd3fa13435b9b358f3be3c6fb7417de467a35a267f4d28ad
COPY start.sh /opt/nifi/ods-start.sh
USER 1000:1000
ENTRYPOINT ["bash", "/opt/nifi/ods-start.sh"]
