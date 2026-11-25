FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

RUN addgroup --gid 10777 odsrouter && \
    adduser --uid 10777 --gid 10777 --disabled-password --gecos "" odsrouter

WORKDIR /srv/model-router
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app ./app
# Normalize image code permissions without changing private host source modes.
RUN chmod -R a+rX /srv/model-router

USER odsrouter
EXPOSE 9099
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9099/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9099", "--no-server-header"]
