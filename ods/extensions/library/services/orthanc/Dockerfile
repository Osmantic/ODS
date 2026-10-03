FROM orthancteam/orthanc:latest@sha256:1ca07ad4a70f67f815e55a558e76718d572ed9d0571268a3fbc1516a9155c5ca
USER root
RUN mkdir -p /var/lib/orthanc/db && chown 999:999 /var/lib/orthanc/db && chmod 0700 /var/lib/orthanc/db
COPY orthanc.json /etc/orthanc/ods.json
COPY start.sh /ods-start.sh
USER 999:999
ENTRYPOINT ["bash", "/ods-start.sh"]
CMD ["/tmp/orthanc.json"]
