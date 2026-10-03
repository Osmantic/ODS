FROM dbeaver/cloudbeaver:25.3.5@sha256:f3cca49f00cab1c8878cf9d0431620ee51a7d52f8ece72b49d60195fdc709747
USER 0
RUN mkdir -p /opt/cloudbeaver/workspace /opt/cloudbeaver/drivers && chown -R 8978:8978 /opt/cloudbeaver/workspace /opt/cloudbeaver/drivers
COPY initial-data-sources.conf /opt/cloudbeaver/conf/initial-data-sources.conf
USER 8978:8978
