FROM apache/couchdb:3.5.2.1@sha256:078affa27ab6b6f99da17137f6aaabe70f598f1a5b1808ab065874672208f89a
COPY --chown=5984:5984 ods.ini /opt/couchdb/etc/local.d/10-ods.ini
COPY ods-health.sh /opt/couchdb/ods-health.sh
USER 5984:5984
