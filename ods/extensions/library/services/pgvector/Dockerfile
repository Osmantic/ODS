FROM pgvector/pgvector:0.8.6-pg17-bookworm@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f
COPY start.sh health.sh /opt/ods/
COPY init.sql /docker-entrypoint-initdb.d/10-ods.sql
ENTRYPOINT ["sh", "/opt/ods/start.sh"]
CMD ["postgres", "-c", "shared_buffers=128MB", "-c", "max_connections=50", "-c", "password_encryption=scram-sha-256"]
