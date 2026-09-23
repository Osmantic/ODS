FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall --yes pip

COPY main.py .

RUN adduser --system --no-create-home ape

EXPOSE 7890

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7890/health')" || exit 1

USER ape

CMD ["python", "main.py"]
