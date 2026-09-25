FROM python:3.11-slim
WORKDIR /srv/pixel-inference
COPY extensions/services/pixel-inference/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY extensions/services/pixel-inference/app ./app
COPY bin/pixel_provider ./pixel_provider
# Compose runs as the installing owner so the read-only private state mount
# remains 0700/0600. No host control credential or Docker socket is mounted.
USER 1000:1000
EXPOSE 8093
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8093", "--workers", "1", "--no-server-header", "--no-access-log"]
