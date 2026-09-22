FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
COPY requirements.txt /opt/ods/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /opt/ods/requirements.txt \
    && mkdir -p /workspace /data/home /data/python && chown -R 1000:1000 /workspace /data
ENV HOME=/data/home PYTHONUSERBASE=/data/python PYTHONDONTWRITEBYTECODE=1 MARIMO_SKIP_UPDATE_CHECK=1
COPY start.py health.py /opt/ods/
USER 1000:1000
WORKDIR /workspace
EXPOSE 8080
CMD ["python", "/opt/ods/start.py"]
