FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git jq python3 ripgrep rsync \
      poppler-utils tesseract-ocr tesseract-ocr-eng \
      python3-docx python3-pil catdoc unzip file xz-utils \
    && rm -rf /var/lib/apt/lists/*
# Node.js for offline JavaScript checks in the agent sandbox: the official
# nodejs.org build for this architecture, pinned by SHA-256 in
# RELEASE-MANIFEST.json (sandboxNodeRuntime) and generated into the rows below.
# Only bin/node and LICENSE are extracted, so npm, npx and corepack never exist.
# Bootstrap's docker build has no timeout, so a stalled download must end on its
# own: under 1 KB/s for 60 s, or 30 minutes per attempt.
# Plain RUN syntax keeps legacy Docker builders working.
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_url=https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz node_sha256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6 ;; \
      arm64) node_url=https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-arm64.tar.xz node_sha256=6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2 ;; \
      *) echo "Pixel sandbox: no pinned Node.js build for $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --proto '=https' --tlsv1.2 --connect-timeout 30 --speed-limit 1024 --speed-time 60 \
      --max-time 1800 --retry 3 --retry-connrefused -o /tmp/node.tar.xz "$node_url" \
      || { echo "Pixel sandbox: could not download $node_url; check access to nodejs.org" >&2; exit 1; }; \
    echo "$node_sha256  /tmp/node.tar.xz" | sha256sum -c - >/dev/null \
      || { echo "Pixel sandbox: $node_url did not match its pinned SHA-256; nothing was installed" >&2; rm -f /tmp/node.tar.xz; exit 1; }; \
    node_dir="${node_url##*/}"; node_dir="${node_dir%.tar.xz}"; \
    mkdir /tmp/node; \
    tar -xJf /tmp/node.tar.xz -C /tmp/node --no-same-owner "$node_dir/bin/node" "$node_dir/LICENSE"; \
    install -m 0755 "/tmp/node/$node_dir/bin/node" /usr/local/bin/node; \
    install -D -m 0644 "/tmp/node/$node_dir/LICENSE" /usr/local/share/doc/nodejs/LICENSE; \
    rm -rf /tmp/node /tmp/node.tar.xz; \
    test "$(/usr/local/bin/node --version)" = "v24.21.0"; \
    for tool in npm npx corepack; do \
      if command -v "$tool" >/dev/null 2>&1; then echo "Pixel sandbox: $tool must not be installed" >&2; exit 1; fi; \
    done
LABEL org.osmantic.pixel.sandbox-node="24.21.0"
ARG PIXEL_SANDBOX_UID=1000
RUN case "$PIXEL_SANDBOX_UID" in ''|*[!0-9]*) exit 1 ;; esac \
    && test "$PIXEL_SANDBOX_UID" -gt 0 \
    && useradd --uid "$PIXEL_SANDBOX_UID" --create-home --shell /bin/bash sandbox
LABEL org.osmantic.pixel.sandbox-uid="$PIXEL_SANDBOX_UID"
USER sandbox
WORKDIR /home/sandbox
CMD ["sleep", "infinity"]
