FROM busybox:1.37.0@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0 AS directories
RUN mkdir -p /prepared/db /prepared/files
FROM vikunja/vikunja:2.6.0@sha256:417ada6f94e81f0267aa2f007d0a811fc82d38dd2aa58351e3ea520ca01c2ea5
# Prepare named-volume ownership without adding a runtime shell or root init.
COPY --from=directories --chown=1000:1000 /prepared/db /db
COPY --from=directories --chown=1000:1000 /prepared/files /app/vikunja/files
USER 1000:1000
