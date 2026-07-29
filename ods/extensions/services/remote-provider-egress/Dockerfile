FROM python:3.11-slim

RUN addgroup --gid 10778 odsremote && \
    adduser --uid 10778 --gid 10778 --disabled-password --gecos "" odsremote

WORKDIR /srv/remote-provider-egress
COPY extensions/services/remote-provider-egress/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY extensions/services/remote-provider-egress/app ./app
COPY bin/remote_provider ./remote_provider

USER odsremote
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091", "--no-server-header"]
