FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY pii_scrubber.py .
COPY proxy.py .
COPY key_management.py .

# Create data directory for session persistence
RUN mkdir -p /data && chown -R 1000:1000 /data

# Run as non-root
USER 1000:1000

# Expose port
EXPOSE 8085

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8085/health')" || exit 1

# Start proxy
CMD ["python3", "proxy.py"]
