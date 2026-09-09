# Bark TTS — builds a minimal FastAPI server wrapping suno-ai/bark
# Multi-arch base (amd64 + arm64). Default pip wheels include CUDA support
# on amd64; the compose.nvidia.yaml overlay provides GPU device access.
FROM python:3.10-slim

# Run as a non-root user, matching the other services in this library.
# The uid is fixed so bind-mounted data keeps a stable owner across rebuilds.
RUN groupadd --gid 10002 bark \
    && useradd --uid 10002 --gid 10002 --create-home --shell /bin/bash bark

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch — default wheels include CUDA on amd64, CPU-only on arm64
RUN pip install --no-cache-dir torch torchaudio

# Install Python dependencies — pinned versions
RUN pip install --no-cache-dir \
    bark==0.1.5 \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    soundfile==0.12.1 \
    numpy==1.26.4

# Copy the API server
COPY server.py /app/server.py

# bark resolves its model cache from the home directory. Older releases use
# expanduser("~/.cache"), newer ones honour XDG_CACHE_HOME; both are set to the
# same path so the ~5GB download lands in the mounted volume either way.
ENV HOME=/home/bark
ENV XDG_CACHE_HOME=/home/bark/.cache

RUN mkdir -p /home/bark/.cache/suno /app/output \
    && chown -R bark:bark /home/bark /app

USER bark

# Pre-download models at build time into the image layer
# (models are ~5GB; they will be cached in the volume on first run if not pre-baked)
# We skip baking them into the image to keep build size manageable.
# On first startup the container downloads them to /home/bark/.cache/suno/bark_v0

EXPOSE 9200

ENV SUNO_USE_SMALL_MODELS=false
ENV SUNO_OFFLOAD_CPU=false

HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:9200/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9200"]
