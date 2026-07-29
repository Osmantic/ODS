FROM python:3.11-slim

RUN addgroup --gid 10777 odsrouter && \
    adduser --uid 10777 --gid 10777 --disabled-password --gecos "" odsrouter

WORKDIR /srv/model-router
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

USER odsrouter
EXPOSE 9099
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9099/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9099", "--no-server-header"]
