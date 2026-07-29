FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN addgroup --gid 10778 odsremote \
    && adduser --uid 10778 --gid 10778 --disabled-password --gecos "" odsremote

WORKDIR /srv/remote-provider-ssh-tunnel
COPY extensions/services/remote-provider-ssh-tunnel/app ./app
COPY bin/remote_provider ./remote_provider

USER odsremote
EXPOSE 18090 18091 18092
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18090/health',timeout=4).status==200 else 1)"
CMD ["python", "-m", "app.main"]
