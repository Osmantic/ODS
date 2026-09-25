# syntax=docker/dockerfile:1
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236 AS source
WORKDIR /source
ADD --checksum=sha256:de7ed18349e4caed8d0025d56a3d620cbe79244f1345b3728089f5b55f834bf3 https://codeload.github.com/pkalogiros/AudioMass/tar.gz/21f5ee1362a47be6f0dbe6e4969a15e43d21b044 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /source && rm -f /source/src/audiomass-server.go /source/src/audiomass-server.py
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
RUN rm -rf /usr/share/nginx/html/*
COPY --from=source /source/src /usr/share/nginx/html
COPY --from=source /source/LICENSE /source/THIRD_PARTY_NOTICES.md /usr/share/nginx/html/licenses/
COPY --from=source /tmp/source.tar.gz /usr/share/nginx/html/source/audiomass-21f5ee1.tar.gz
COPY nginx.conf /etc/nginx/nginx.conf
USER 101:101
