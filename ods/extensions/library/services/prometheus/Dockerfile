FROM prom/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0
COPY prometheus.yml /opt/ods/default-prometheus.yml
COPY start.sh /opt/ods/start.sh
ENTRYPOINT ["/bin/sh", "/opt/ods/start.sh"]
CMD []
