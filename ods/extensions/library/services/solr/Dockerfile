FROM solr:latest@sha256:2476a4aecd7629ac853df4139b5864797677f2fe123b92116e18b01b5bf73c08
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY security.py /opt/ods-security.py
COPY security.sh /docker-entrypoint-initdb.d/90-ods-security.sh
USER 8983:8983
