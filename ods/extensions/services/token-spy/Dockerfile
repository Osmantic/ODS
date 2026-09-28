FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

RUN mkdir -p /app/data

# Create non-root user and set ownership
RUN adduser --system --no-create-home --uid 1000 odser && \
    chown -R odser:nogroup /app

USER odser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${UVICORN_PORT:-8080}/health')" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${UVICORN_PORT:-8080} --log-level info"]
