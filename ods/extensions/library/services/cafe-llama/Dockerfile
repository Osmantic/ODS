# Optional cafe-llama.cpp advanced runtime image.
# Prefers a prebuilt llama-server from upstream releases.
# When CAFE_LLAMA_RELEASE_URL is empty, downloads the latest known
# Linux x64 CUDA build (0.75) so `ods enable cafe-llama` works out of the box
# on typical NVIDIA hosts. Override the ARG for other platforms / backends.
FROM ubuntu:24.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl file tar \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cafe-llama

# Default: official 0.75 Linux x64 CUDA 12.4 build (sm50-sm90).
# Override at build time for Vulkan / ROCm / arm64 / CPU-only / newer tags.
ARG CAFE_LLAMA_RELEASE_URL="https://github.com/quimmedes/cafe-llama.cpp/releases/download/0.75/llama-bin-ubuntu-x64-cuda-12.4.zip"

RUN set -eux; \
    if [ -n "$CAFE_LLAMA_RELEASE_URL" ]; then \
      echo "Downloading cafe-llama from $CAFE_LLAMA_RELEASE_URL"; \
      curl -fsSL "$CAFE_LLAMA_RELEASE_URL" -o /tmp/cafe-asset; \
      # Accept both .zip and .tar.gz assets
      if file /tmp/cafe-asset | grep -qi zip; then \
        apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*; \
        unzip -q /tmp/cafe-asset -d /opt/cafe-llama; \
      else \
        tar -xzf /tmp/cafe-asset -C /opt/cafe-llama; \
      fi; \
      rm -f /tmp/cafe-asset; \
      # Normalize binary location
      if [ ! -x /opt/cafe-llama/llama-server ]; then \
        found=$(find /opt/cafe-llama -type f -name llama-server -print -quit || true); \
        if [ -n "$found" ]; then cp "$found" /usr/local/bin/llama-server; \
        else echo "WARNING: llama-server not found in asset" >&2; fi; \
      else \
        cp /opt/cafe-llama/llama-server /usr/local/bin/llama-server; \
      fi; \
      chmod +x /usr/local/bin/llama-server 2>/dev/null || true; \
    fi

COPY entrypoint.sh /usr/local/bin/cafe-llama-entrypoint.sh
RUN chmod +x /usr/local/bin/cafe-llama-entrypoint.sh

EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/cafe-llama-entrypoint.sh"]
