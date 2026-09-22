# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM maven:3.9-eclipse-temurin-21@sha256:c2a2c58516d160f43b50f12baa427ca86989e0bc942609e04aff61da5d9a7d74 AS build
WORKDIR /src
ADD --checksum=sha256:4eb77f988f2ad75bd3b4ab4cd9565bea08e57f53e7cb17f71ff0e8f8c6b77580 https://codeload.github.com/languagetool-org/languagetool/tar.gz/refs/tags/v6.6 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && mvn -B -ntp -pl languagetool-standalone -am package -DskipTests -Dmaven.gitcommitid.skip=true \
    && mkdir /out && cd /out \
    && jar -xf /src/languagetool-standalone/target/LanguageTool-6.6.zip \
    && test -s /out/LanguageTool-6.6/languagetool-server.jar
FROM eclipse-temurin:21-jre-alpine@sha256:1a29e1fe337eb28b5bec30f0ee8ed29f0ff80ab6f75dcf9313efe82911065a52
RUN apk add --no-cache curl
COPY --from=build /out/LanguageTool-6.6 /app
COPY server.properties /app/server.properties
WORKDIR /app
USER 1000:1000
ENTRYPOINT ["java", "-Xms128m", "-Xmx1536m", "-Djava.awt.headless=true", "-cp", "languagetool-server.jar", "org.languagetool.server.HTTPServer", "--config", "server.properties", "--port", "8081", "--public"]
