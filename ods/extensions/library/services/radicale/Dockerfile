FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY requirements.txt /opt/ods/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /opt/ods/requirements.txt \
    && mkdir -p /data/collections && chown -R 1000:1000 /data
COPY config /etc/radicale/config
COPY start.py health.py /opt/ods/
USER 1000:1000
EXPOSE 5232
CMD ["python", "/opt/ods/start.py"]
