# ODS Dashboard API
# Lightweight system status backend for the Dashboard UI

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/Osmantic/ODS"
LABEL org.opencontainers.image.description="ODS Dashboard API"

WORKDIR /app

# Install system deps for GPU metrics access
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
# Use glob to pick up every top-level .py file. The old explicit list
# went stale every time a new module landed in main.py's imports —
# `session_signer.py` was missed once (see #1181), then `settings.py`
# again right after. The glob is bounded to the build context's top
# level (won't recurse into routers/, tests/, etc.) and matches the
# filesystem shape (one flat module dir per service).
COPY *.py ./
COPY performance_evidence.json ./
COPY routers/ routers/

# Non-root user
RUN useradd -m -u 1000 odser

# B1 fix: Create /data directory and make it writable for API key generation
RUN mkdir -p /data && chown odser:odser /data

USER odser

ENV DASHBOARD_API_PORT=3002

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${DASHBOARD_API_PORT}/health || exit 1

EXPOSE ${DASHBOARD_API_PORT}

CMD uvicorn main:app --host 0.0.0.0 --port ${DASHBOARD_API_PORT}
