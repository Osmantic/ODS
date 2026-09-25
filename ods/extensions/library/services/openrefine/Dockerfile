# syntax=docker/dockerfile:1
FROM eclipse-temurin:21-jre-alpine@sha256:1a29e1fe337eb28b5bec30f0ee8ed29f0ff80ab6f75dcf9313efe82911065a52
RUN apk add --no-cache bash grep procps curl fontconfig ttf-dejavu \
    && mkdir -p /opt/openrefine /data/projects \
    && chown -R 1000:1000 /data
ADD --checksum=sha256:806e38e415fa98c2dee400de575992514557a4e37e82c670b7cd2e34d667c4ba https://github.com/OpenRefine/OpenRefine/releases/download/3.10.1/openrefine-linux-3.10.1.tar.gz /tmp/openrefine.tar.gz
RUN tar -xzf /tmp/openrefine.tar.gz --strip-components=1 -C /opt/openrefine \
    && rm /tmp/openrefine.tar.gz
COPY refine.ini /opt/openrefine/ods-refine.ini
WORKDIR /opt/openrefine
ENV HOME=/data
USER 1000:1000
ENTRYPOINT ["/bin/bash", "./refine", "-c", "/opt/openrefine/ods-refine.ini"]
