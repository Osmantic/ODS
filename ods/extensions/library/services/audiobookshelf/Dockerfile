FROM ghcr.io/advplyr/audiobookshelf:2.36.1@sha256:3528a93b6442ffe54bd46771bbbab7c97084e1101071586d9dc2254f30bb4358
RUN mkdir -p /config /metadata /audiobooks /podcasts && chown 1000:1000 /config /metadata /audiobooks /podcasts
USER 1000:1000
