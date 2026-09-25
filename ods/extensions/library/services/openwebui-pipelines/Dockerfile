FROM ghcr.io/open-webui/pipelines:main@sha256:b48e9bc338ce2be0acfbeff01810db72408a12f07739f9e3879c1f2b00952d6e
RUN mkdir -p /data/pipelines /data/python /data/cache && chown -R 1000:1000 /data
COPY start.py health.py /opt/ods/
ENV PIPELINES_DIR=/data/pipelines PYTHONUSERBASE=/data/python HOME=/data PYTHONDONTWRITEBYTECODE=1 PIP_CACHE_DIR=/data/cache
USER 1000:1000
ENTRYPOINT ["python", "/opt/ods/start.py"]
