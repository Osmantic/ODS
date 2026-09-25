FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq python3 ripgrep rsync \
      poppler-utils tesseract-ocr tesseract-ocr-eng \
      python3-docx python3-pil catdoc unzip file \
    && rm -rf /var/lib/apt/lists/*
ARG PIXEL_SANDBOX_UID=1000
RUN case "$PIXEL_SANDBOX_UID" in ''|*[!0-9]*) exit 1 ;; esac \
    && test "$PIXEL_SANDBOX_UID" -gt 0 \
    && useradd --uid "$PIXEL_SANDBOX_UID" --create-home --shell /bin/bash sandbox
LABEL org.osmantic.pixel.sandbox-uid="$PIXEL_SANDBOX_UID"
USER sandbox
WORKDIR /home/sandbox
CMD ["sleep", "infinity"]
